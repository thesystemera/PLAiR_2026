import asyncio
import random
import re
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from config.settings import settings
from database.pg_pool import get_pooled_connection
from models_global import get_sentence_encoder, run_on_gpu_executor
from services import log_service, usage_tracking
from services.task_utils import spawn
from services_radio.dj_prompt_helper_service import assemble_prompt, clean_gpt_output, is_valid_dj_script
from services_radio.tts_stream_planner import spoken_text

IMPULSE = 'impulse'
INTERLUDE = 'interlude'
KINDS = (IMPULSE, INTERLUDE)
HOSTS = ('LEO', 'JESS')
NEAR_BEST = 0.01
AVOID_EXAMPLES = 8
SHOW_STYLE_NODES = ['core_dj_identity', 'format_roles_detailed', 'format_tone', 'format_performance_tags_guide',
                    'format_performance_tag_examples', 'format_dialogue_examples']
PERFORMANCE_TAGS = re.compile(r'~[^~]*~|%[^%]*%|@[WwCc]?[\d.]+@|&[\d.]+&|\[[A-Z /]+]')


def plain_talk(text: str) -> str:
    text = re.split(r'\[INTERNAL', text or '', maxsplit=1)[0]
    return " ".join(PERFORMANCE_TAGS.sub(' ', spoken_text(text)).split())


def conversation_context(current: str, history: List[Tuple[str, str]], turns: int, chars: int) -> str:
    lines = [f"NOW listener: {current.strip()[:chars * 2]}"]
    for listener, hosts in reversed(history[-turns:]):
        lines.append(f"EARLIER listener: {listener[:chars]}")
        if hosts:
            lines.append(f"EARLIER hosts: {hosts[:chars]}")
    return "\n".join(lines)


@dataclass
class FillerScript:
    id: int
    kind: str
    context: str
    script: str
    group: Optional[str]
    index: int
    vector: np.ndarray


@dataclass
class Wait:
    session_dict: Dict
    user_id: Optional[int]
    is_guest: bool
    context: str
    activity: str = ""
    played: int = 0
    group: Optional[str] = None
    index: int = -1
    in_flight: bool = False


class FillerScripts:
    """Impulse and interlude scripts: little bits of radio theatre, picked by the conversation they fit.

    A pick always takes the closest stored script (never waits on an LLM); a weak or empty match writes
    a better one in the background for next time. The audio comes from the normal line cache.
    """

    def __init__(self, writer, generation, queue, dj_service):
        self.writer = writer
        self.dj_service = dj_service
        self.generation = generation
        self.queue = queue
        self.encoder = get_sentence_encoder(settings.SEMANTIC_ENCODER)
        self._scripts: Dict[str, List[FillerScript]] = {kind: [] for kind in KINDS}
        self._matrix: Dict[str, Optional[np.ndarray]] = {kind: None for kind in KINDS}
        self._learning: set = set()
        self._waits: Dict[str, Wait] = {}
        self._monitor: Optional[asyncio.Task] = None

    def load(self):
        conn = get_pooled_connection(settings.EMBEDDINGS_DATABASE_URL)
        try:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS filler_scripts (
                    id SERIAL PRIMARY KEY,
                    kind TEXT NOT NULL,
                    context TEXT NOT NULL,
                    script TEXT NOT NULL,
                    sequence_group TEXT,
                    sequence_index INTEGER NOT NULL DEFAULT 0,
                    embedding BYTEA NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            conn.commit()
            c.execute("SELECT id, kind, context, script, sequence_group, sequence_index, embedding FROM filler_scripts"
                      " WHERE octet_length(embedding) = %s ORDER BY id", (settings.SEMANTIC_ENCODER_DIM * 4,))
            rows = c.fetchall()
        finally:
            conn.close()
        for row_id, kind, context, script, group, index, embedding in rows:
            if kind in self._scripts:
                self._scripts[kind].append(FillerScript(row_id, kind, context, script, group, index,
                                                        np.frombuffer(bytes(embedding), dtype=np.float32)))
        for kind in KINDS:
            self._rebuild(kind)
        log_service.tts_vector_db("Filler scripts: " + ", ".join(f"{len(self._scripts[k])} {k}" for k in KINDS))

    def _rebuild(self, kind: str):
        scripts = self._scripts[kind]
        self._matrix[kind] = np.stack([s.vector for s in scripts]) if scripts else None

    def _embed(self, text: str) -> np.ndarray:
        return self.encoder.encode(text, normalize_embeddings=True, convert_to_numpy=True).astype(np.float32)

    @staticmethod
    def _shotgun_key(script: FillerScript) -> str:
        return f"filler_script:{script.id}"

    def _fresh(self, listener: str, script: FillerScript, now: float) -> bool:
        return self.generation.vector_db_service.is_fresh(listener, self._shotgun_key(script), now)

    def _mark(self, listener: str, script: FillerScript, now: float):
        self.generation.vector_db_service.note_used(listener, self._shotgun_key(script), now)

    async def pick(self, kind: str, context: str, listener: str, group: Optional[str] = None,
                   after: int = -1) -> Optional[FillerScript]:
        now = time.time()
        if group:
            follow = min((s for s in self._scripts[kind] if s.group == group and s.index > after),
                         key=lambda s: s.index, default=None)
            if follow is not None:
                self._mark(listener, follow, now)
            return follow

        matrix = self._matrix[kind]
        best, best_similarity = None, None
        if matrix is not None:
            query = await run_on_gpu_executor(self._embed, context)
            similarities = matrix @ query
            order = list(np.argsort(-similarities))
            eligible = [i for i in order if self._fresh(listener, self._scripts[kind][i], now)] or order
            top = float(similarities[eligible[0]])
            choice = random.choice([i for i in eligible if top - float(similarities[i]) < NEAR_BEST])
            best, best_similarity = self._scripts[kind][choice], float(similarities[choice])
            if best.group:
                best = min((s for s in self._scripts[kind] if s.group == best.group), key=lambda s: s.index)
        if best_similarity is None or best_similarity < settings.FILLER_LEARN_BELOW:
            self.learn(kind, context)
        if best is not None:
            self._mark(listener, best, now)
        return best

    def learn(self, kind: str, context: str):
        key = (kind, context[:300])
        if key in self._learning or len(self._learning) >= settings.FILLER_LEARN_MAX_PENDING:
            return
        self._learning.add(key)
        spawn(self._learn(key, kind, context), name=f"filler_learn:{kind}")

    async def _learn(self, key, kind: str, context: str):
        usage_tracking.bind(usage_tracking.system_subject("tts_library"))
        try:
            async with self.generation.background_slot():
                lead = random.choice(HOSTS)
                cohost = HOSTS[1 - HOSTS.index(lead)]
                from services_radio.context_node_registry import node_registry
                nodes = await node_registry.fetch_nodes(SHOW_STYLE_NODES, dj_service=self.dj_service)
                show_style = assemble_prompt(nodes, SHOW_STYLE_NODES, untrusted_keys=set(), note=None)
                avoid = [plain_talk(s.script) for s in self._scripts[kind][-AVOID_EXAMPLES:]]
                if kind == IMPULSE:
                    written = await self.writer.write_impulse_script(show_style, context, lead, cohost, avoid)
                    drafts = [written] if written else []
                else:
                    drafts = await self.writer.write_interlude_scripts(
                        show_style, context, lead, cohost, avoid, settings.INTERLUDE_SEQUENCE) or []
                scripts = [self._finish(draft) for draft in drafts]
                scripts = [script for script in scripts if script]
                if not scripts:
                    log_service.tts_vector_db(f"Filler scripts: {kind} draft rejected for '{context[:60]}'")
                    return
                group = uuid.uuid4().hex[:12] if kind == INTERLUDE else None
                vector = await run_on_gpu_executor(self._embed, context)
                ids = await asyncio.to_thread(self._save, kind, context, scripts, group, vector)
                self._scripts[kind].extend(FillerScript(row_id, kind, context, script, group, index, vector)
                                           for index, (row_id, script) in enumerate(zip(ids, scripts)))
                self._rebuild(kind)
                log_service.tts_vector_db(
                    f"Filler scripts: wrote {len(scripts)} {kind} for '{context.splitlines()[0][:60]}' "
                    f"| \"{plain_talk(scripts[0])[:80]}\"")
        except Exception as e:
            log_service.error(f"Filler scripts: writing a {kind} failed: {e}")
        finally:
            self._learning.discard(key)

    @staticmethod
    def _finish(draft: str) -> Optional[str]:
        script = clean_gpt_output(draft or "", role='dj_content').strip()
        if not script.startswith('[BROADCAST]'):
            script = f"[BROADCAST] {script}"
        return script if is_valid_dj_script(script) else None

    @staticmethod
    def _save(kind: str, context: str, scripts: List[str], group: Optional[str], vector: np.ndarray) -> List[int]:
        conn = get_pooled_connection(settings.EMBEDDINGS_DATABASE_URL)
        try:
            c = conn.cursor()
            ids = []
            for index, script in enumerate(scripts):
                c.execute("INSERT INTO filler_scripts (kind, context, script, sequence_group, sequence_index, embedding)"
                          " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                          (kind, context, script, group, index, vector.tobytes()))
                ids.append(c.fetchone()[0])
            conn.commit()
            return ids
        finally:
            conn.close()

    async def play(self, kind: str, script: FillerScript, user_id: Optional[int], session_id: str,
                   is_guest: bool, session_dict: Dict):
        session_dict.setdefault('on_air', []).append(script.script)
        await self.queue.add_tts_request(text=script.script, user_id=user_id or 0, tts_type=kind,
                                         is_broadcast=True, is_temp_user=is_guest, session_id=session_id)
        ctx = session_dict.get('turn_ctx')
        if ctx is not None:
            await ctx.activity("say", text=script.script)

    def begin_wait(self, session_id: str, user_id: Optional[int], is_guest: bool, session_dict: Dict, context: str):
        self._waits[session_id] = Wait(session_dict, user_id, is_guest, context)
        if self._monitor is None or self._monitor.done():
            self._monitor = spawn(self._watch(), name="interlude_monitor")

    def note_activity(self, session_id: str, activity: str):
        wait = self._waits.get(session_id)
        if wait is not None and activity:
            wait.activity = activity

    def end_wait(self, session_id: str, session_dict: Dict):
        wait = self._waits.get(session_id)
        if wait is not None and wait.session_dict is session_dict:
            del self._waits[session_id]

    async def _watch(self):
        while self._waits:
            await asyncio.sleep(settings.INTERLUDE_MONITOR_INTERVAL_S)
            for session_id, wait in list(self._waits.items()):
                if wait.in_flight or wait.played >= settings.INTERLUDE_MAX_PER_TURN:
                    continue
                if self.queue.quiet_for(session_id, wait.user_id or 0) < settings.INTERLUDE_SILENCE_S:
                    continue
                wait.in_flight = True
                spawn(self._interlude(session_id, wait), name=f"interlude:{session_id}")

    async def _interlude(self, session_id: str, wait: Wait):
        try:
            context = f"{wait.context}\nWHAT THE HOSTS ARE DOING: {wait.activity}" if wait.activity else wait.context
            script = await self.pick(INTERLUDE, context, session_id, wait.group, wait.index)
            wait.played += 1
            if script is None:
                wait.played = settings.INTERLUDE_MAX_PER_TURN
                return
            wait.group, wait.index = script.group, script.index
            if self._waits.get(session_id) is wait:
                await self.play(INTERLUDE, script, wait.user_id, session_id, wait.is_guest, wait.session_dict)
        except Exception as e:
            log_service.error(f"Interlude for {log_service.who(session_id)} failed: {e}")
        finally:
            wait.in_flight = False
