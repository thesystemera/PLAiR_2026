from services import log_service
from services.llm_router import LLM_ANNOUNCE, LLM_BACKGROUND

VOICE_SCRIPT_ROLE = LLM_ANNOUNCE
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
                                  role: str) -> str:
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
            ],
            role=VOICE_SCRIPT_ROLE
        )

        impulse_response = impulse_response.strip()
        log_service.api(f"Impulse: IMPULSE ASSISTANT ({responding_dj}): {impulse_response}")

        words = impulse_response.split()
        if len(words) > 10:
            impulse_response = ' '.join(words[:10])

        return user_input, impulse_response

    @gpt_error_handler
    async def generate_paralanguage_gpt_response(self, paralanguage_tag):
        system_prompt = (
            "You are a language model responsible for converting paralanguage tags or action descriptions into phonetic or "
            "onomatopoeic prompts suitable for text-to-speech synthesis.\n"
            "Your task is to take the provided paralanguage tag or action description and generate a concise prompt that represents "
            "the intended action or sound using only phonetic transcriptions or onomatopoeic words, without including any "
            "English words, the original paralanguage tag/action description, or any tag syntax such as asterisks (*).\n"
            "When generating the prompt, focus solely on capturing the verbal sounds or noises associated with the action, "
            "rather than providing descriptive phrases.\n\n"
            "Examples:\n"
            "- For the paralanguage tag 'scratches head', the prompt could be 'aahha-aha-mmm'\n"
            "- For 'pauses briefly', the prompt could be '.......oooo......'\n"
            "- For 'laughs hysterically', the prompt could be 'phhaaahhhaahhaahahaha...'\n"
            "- For 'clears throat', the prompt could be '---h-hm---'\n"
            "- For 'pleasure', the prompt could be '....aaooowwwhwhwh....'\n"
            "- For 'laughs sincerely', the prompt could be 'hhhhaaaahhhaaahhaahaha'\n"
            "- For 'approves', the prompt could be 'mmmnnn-mmnn-mn'\n"
            "- For 'annoyed', the prompt could be 'aarrrgg....'\n"
            "- For 'shocked', the prompt could be 'ffaarrrkk'\n"
            "- For 'disgusted', the prompt could be '....eeeeaak...'\n"
            "- For 'nods', the prompt could be 'mm-hmm'\n"
            "- For 'playful', the prompt could be '--phhth--'\n"
            "- For 'smirks', the prompt could be 'hmph'\n"
            "Do not use any English words, tag syntax, or the original paralanguage tag/action description in the generated prompt. "
            "Respond with the generated prompt only, without any additional context or explanation."
        )
        if settings.PARALANGUAGE_ENGINE_TAGS:
            engine_tags = " ".join(f"[{tag}]" for tag in settings.ENGINE_SOUND_TAGS)
            system_prompt += (
                "\n\nThe voice engine can also perform these sounds natively when you write the tag in square brackets: "
                f"{engine_tags}\n"
                "Decide what renders the action best: the engine tag alone, the engine tag followed by your phonetic "
                "prompt (when the tag is close but the action asks for more, e.g. 'laughs out loud' -> "
                "[laugh] hhaahhahaha-haa), or your phonetic prompt alone (when no tag fits). "
                "Use only tags from this list, written exactly as shown; these square brackets are the one exception "
                "to the syntax rule above."
            )

        paralanguage_prompt = await self._execute_gpt_stream(
            model=self.config['dj_model'],
            max_tokens=self.config['dj_tokens'],
            temperature=self.config['dj_temperature'],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": paralanguage_tag}
            ],
            role=VOICE_SCRIPT_ROLE
        )

        paralanguage_prompt = paralanguage_prompt.strip() if paralanguage_prompt else ""
        if not paralanguage_prompt:
            log_service.gpt(f"Paralanguage: No prompt generated for tag: {paralanguage_tag}")
            return None
        log_service.gpt(f"Paralanguage: {paralanguage_tag} -> {paralanguage_prompt}")
        return paralanguage_tag, paralanguage_prompt

    @gpt_error_handler
    async def generate_paralanguage_emoji(self, reaction):
        system_prompt = (
            "You pick emoji for a radio DJ's non-verbal vocal reaction (a paralanguage tag). "
            "Reply with one or two emoji that show the sound or feeling of the reaction, and nothing else."
        )
        return await self._execute_gpt_stream(
            model=self.config['dj_model'],
            max_tokens=settings.DJ_MICRO_MAX_TOKENS,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": reaction}
            ],
            role=LLM_BACKGROUND
        )

    @gpt_error_handler
    async def generate_breath_gpt_response(self, context):
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
            role=VOICE_SCRIPT_ROLE
        )

        breath_prompt = breath_prompt.strip().strip("'\"") if breath_prompt else ""
        if not breath_prompt or any(ch.isalpha() and ch not in "hmaeoufsp" for ch in breath_prompt.lower()):
            log_service.gpt(f"Breath: Rejected breath prompt for context: {context[:60]} -> {breath_prompt[:40]}")
            return None
        log_service.gpt(f"Breath: {context[:60]} -> {breath_prompt}")
        return context, breath_prompt
