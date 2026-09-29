from services import log_service
from services.llm_router import LLM_LIVE, LLM_BACKGROUND
from services.llm_result_cache import breath_script_cache, cache_key, meta_script_cache
from config.settings import settings

def gpt_error_handler(func):
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            log_service.error(f"GPT error in {func.__name__}: {e}")
            return None

    return wrapper

class DJPromptSystemService:
    def __init__(self, gemini_service):
        self.gemini_service = gemini_service
        self.config = {
            'dj_model': settings.GEMINI_DJ_MODEL,
            'dj_temperature': settings.GEMINI_DJ_TEMPERATURE,
            'dj_tokens': settings.GEMINI_DJ_MAX_TOKENS
        }

    async def _execute_gpt_stream(self, model: str, max_tokens: int, temperature: float, messages: list,
                                  role: str = LLM_LIVE) -> str:
        system_content = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        user_content = messages[1]["content"] if len(messages) > 1 and messages[1]["role"] == "user" else messages[0][
            "content"]

        response = await self.gemini_service.call_gemini(
            prompt=user_content,
            system_instruction=system_content,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            role=role
        )
        return response or ""

    @gpt_error_handler
    async def generate_impulse_gpt_response(self, user_input, responding_voice):
        responding_dj = "Jess" if responding_voice == "jess" else "Leo"
        other_dj = "Leo" if responding_dj == "Jess" else "Jess"

        system_prompt = (
            f"You are {responding_dj}, one of the quick-witted DJs at PLAiR.fm, co-hosting with {other_dj}. "
            "You're part of a rebellious pirate radio station broadcasting from an undisclosed location. "
            "You've just received a text from a listener and need to give a single, quick, off-the-cuff response "
            f"while you or {other_dj} prepares a more detailed answer.\n\n"
            "Your response should be:\n"
            "1. A single, casual, and spontaneous reaction, as if you've just seen the message\n"
            "2. A brief acknowledgment or initial reaction to the listener's input\n"
            "3. For direct questions, use playful deflection or mention that you or your co-host will look into it\n"
            "4. Incorporate radio jargon, music references, or mild swearing when appropriate\n"
            "5. Don't shy away from controversial topics, but maintain a balance\n"
            f"6. Feel free to mention or involve {other_dj} in your response\n\n"
            "Examples of single responses:\n"
            f"- 'Whoa, hold up! Let me get {other_dj} on this...'\n"
            "- 'Holy sh*t, did you just ask about...? Give us a sec!'\n"
            f"- 'Well, that's a spicy one! {other_dj}'s gonna love this.'\n"
            "- 'Damn, listener! You're keeping us on our toes!'\n"
            f"- 'Huh? Oh, right! Let me nudge {other_dj} and we'll get to that.'\n"
            f"- '{other_dj}, you hearing this? Our listeners are wild!'\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. Provide only ONE response, not a list of options.\n"
            "2. Keep your response casual and spontaneous, between TWO to TEN words.\n"
            "3. Do not include any explanations or additional commentary.\n"
            "4. Respond as if you're speaking directly to the listener in real-time."
        )

        log_service.gpt("Impulse: IMPULSE GPT System Prompt: " + system_prompt)
        log_service.gpt("Impulse: IMPULSE GPT User Input: " + user_input)

        impulse_response = await self._execute_gpt_stream(
            model=self.config['dj_model'],
            max_tokens=settings.DJ_MICRO_MAX_TOKENS,
            temperature=self.config['dj_temperature'],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input}
            ]
        )

        impulse_response = impulse_response.strip()
        log_service.api(f"Impulse: IMPULSE ASSISTANT ({responding_dj}): {impulse_response}")

        words = impulse_response.split()
        if len(words) > 10:
            impulse_response = ' '.join(words[:10])

        return user_input, impulse_response

    @gpt_error_handler
    async def generate_meta_data_gpt_response(self, meta_tag):
        meta_key = cache_key(meta_tag.strip().lower())
        cached_script = meta_script_cache.get(meta_key)
        if cached_script:
            log_service.gpt(f"Meta: {meta_tag} -> {cached_script} (cached)")
            return meta_tag, cached_script

        system_prompt = (
            "You convert a radio DJ's stage direction (a meta tag describing a non-verbal reaction) into a very short "
            "script for the Orpheus text-to-speech engine.\n\n"
            "Orpheus renders these inline emotion tags as real vocal sounds: "
            "<laugh> <chuckle> <sigh> <gasp> <groan> <yawn> <cough> <sniffle>.\n\n"
            "Rules:\n"
            "1. Use one or two emotion tags, optionally with a short natural interjection "
            "(e.g. 'Mm-hmm.', 'Ooh.', 'Ha!', 'Whoa.', 'Ugh.', 'Hmm.').\n"
            "2. Never write phonetic spellings of sounds (no 'hahaha', 'aarrgg', 'phhth').\n"
            "3. Never repeat the stage direction itself. Maximum six words plus tags.\n\n"
            "Examples:\n"
            "- 'laughs hysterically' -> '<laugh> Oh man! <laugh>'\n"
            "- 'laughs sincerely' -> '<chuckle> Ha!'\n"
            "- 'sighs' -> '<sigh>'\n"
            "- 'clears throat' -> '<cough> Right.'\n"
            "- 'nods' -> 'Mm-hmm.'\n"
            "- 'shocked' -> '<gasp> Whoa!'\n"
            "- 'annoyed' -> '<groan> Ugh.'\n"
            "- 'tired' -> '<yawn>'\n"
            "- 'approves' -> 'Mmm, yeah.'\n"
            "- 'smirks' -> '<chuckle> Hmm.'\n\n"
            "Respond with the script only."
        )

        meta_data_prompt = await self._execute_gpt_stream(
            model=self.config['dj_model'],
            max_tokens=settings.DJ_MICRO_MAX_TOKENS,
            temperature=self.config['dj_temperature'],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": meta_tag}
            ]
        )

        meta_data_prompt = meta_data_prompt.strip().strip("'\"") if meta_data_prompt else ""
        if not meta_data_prompt:
            log_service.gpt(f"Meta: No prompt generated for meta tag: {meta_tag}")
            return None
        log_service.gpt(f"Meta: {meta_tag} -> {meta_data_prompt}")
        meta_script_cache.set(meta_key, meta_data_prompt)
        return meta_tag, meta_data_prompt

    @gpt_error_handler
    async def generate_breath_gpt_response(self, context):
        breath_key = cache_key((context or "").strip())
        cached_breath = breath_script_cache.get(breath_key)
        if cached_breath:
            log_service.gpt(f"Breath: {(context or '')[:60]} -> {cached_breath} (cached)")
            return context, cached_breath

        system_prompt = (
            "You generate the micro-sound a radio DJ makes BETWEEN sentences - the tiniest inhale, "
            "a barely-audible lip part, a quarter-second breath. This is NOT a word. It is the "
            "sound of someone catching a micro-beat before their next sentence. Almost subliminal.\n\n"
            "The sentence the DJ just spoke is provided in the user message. Match the emotional tone:\n"
            "- Solemn or delivering difficult news -> a heavier exhale, a weighted pause\n"
            "- Light or matter-of-fact -> barely-there, a flicker\n"
            "- Excited or upbeat -> a quick energetic inhale\n"
            "- Sympathetic or gentle -> a soft, warm breath\n\n"
            "All breaths include a subtle vocalisation - ...m  ..ah  -hff\n\n"
            "Examples:\n"
            "- ...hh\n"
            "- --hm\n"
            "- ..mm\n"
            "- -hff\n"
            "- ..ah\n"
            "CRITICAL: All breaths include a subtle sound - never bare punctuation alone. "
            "No English words. Barely there.\n"
            "Respond with the prompt only, no additional text."
        )

        user_message = (
            f"Sentence: \"{context}\"\nGenerate a breath sound that matches the emotional tone of this sentence."
            if context else "generate a between-sentence breath sound"
        )

        breath_prompt = await self._execute_gpt_stream(
            model=self.config['dj_model'],
            max_tokens=settings.DJ_MICRO_MAX_TOKENS,
            temperature=self.config['dj_temperature'],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            role=LLM_BACKGROUND
        )

        breath_prompt = breath_prompt.strip().strip("'\"") if breath_prompt else ""
        if not breath_prompt or any(ch.isalpha() and ch not in "hmaeoufsp" for ch in breath_prompt.lower()):
            log_service.gpt(f"Breath: Rejected breath prompt for context: {context[:60]} -> {breath_prompt[:40]}")
            return None
        log_service.gpt(f"Breath: {context[:60]} -> {breath_prompt}")
        breath_script_cache.set(breath_key, breath_prompt)
        return context, breath_prompt
