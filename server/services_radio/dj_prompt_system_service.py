import re
from typing import Optional, Tuple
from services import log_service
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
            'dj_tokens': settings.GEMINI_DJ_MAX_TOKENS,
            'audio_model': settings.GEMINI_AUDIO_MODEL,
            'audio_temperature': settings.GEMINI_AUDIO_TEMPERATURE,
            'audio_tokens': settings.GEMINI_AUDIO_MAX_TOKENS
        }

    async def _execute_gpt_stream(self, model: str, max_tokens: int, temperature: float, messages: list) -> str:
        system_content = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        user_content = messages[1]["content"] if len(messages) > 1 and messages[1]["role"] == "user" else messages[0][
            "content"]

        response = await self.gemini_service.call_gemini(
            prompt=user_content,
            system_instruction=system_content,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens
        )
        return response or ""

    @gpt_error_handler
    async def generate_impulse_gpt_response(self, user_input, responding_voice):
        responding_dj = "Terry" if responding_voice == "terry" else "Shaquille"
        other_dj = "Shaquille" if responding_dj == "Terry" else "Terry"

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
            max_tokens=self.config['dj_tokens'],
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
        system_prompt = (
            "You are a language model responsible for converting meta tags or action descriptions into phonetic or "
            "onomatopoeic prompts suitable for text-to-speech synthesis.\n"
            "Your task is to take the provided meta tag or action description and generate a concise prompt that represents "
            "the intended action or sound using only phonetic transcriptions or onomatopoeic words, without including any "
            "English words, the original meta tag/action description, or any meta tag syntax such as asterisks (*).\n"
            "When generating the prompt, focus solely on capturing the verbal sounds or noises associated with the action, "
            "rather than providing descriptive phrases.\n\n"
            "Examples:\n"
            "- For the meta tag 'scratches head', the prompt could be 'aahha-aha-mmm'\n"
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
            "Do not use any English words, meta tag syntax, or the original meta tag/action description in the generated prompt. "
            "Respond with the generated prompt only, without any additional context or explanation."
        )

        meta_data_prompt = await self._execute_gpt_stream(
            model=self.config['dj_model'],
            max_tokens=self.config['dj_tokens'],
            temperature=self.config['dj_temperature'],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": meta_tag}
            ]
        )

        meta_data_prompt = meta_data_prompt.strip() if meta_data_prompt else ""
        if meta_data_prompt:
            log_service.info(f"Meta: Input meta tag: {meta_tag}")
            log_service.gpt(f"Meta: Generated prompt: {meta_data_prompt}")
        else:
            log_service.gpt(f"Meta: No prompt generated for meta tag: {meta_tag}")
            return None
        return meta_tag, meta_data_prompt

    @gpt_error_handler
    async def generate_audio_effects_gpt_response(self, audio_tag: str) -> Tuple[
        Optional[str], Optional[str], Optional[float]]:
        MAX_DESCRIPTION_LENGTH = 425
        system_prompt = (
            "You are an audio description specialist for PLAiR.fm, our radio station. Provide brief, vivid descriptions "
            "for two distinct categories of audio. Radio style sound-effects and natural studio environment noises.\n\n"
            "1. Radio Stabs, Stingers (Sound Effects):\n"
            "   - Short, attention-grabbing sounds used for emphasis or transitions\n"
            "   - Incorporate the audio tag naturally into the description\n"
            "   - Describe in 5-10 words\n"
            "   - Duration: 1-3 seconds\n\n"
            "2. In-Studio Activity (Environmental Noise):\n"
            "   - Natural, ambient sounds that occur in a radio studio environment\n"
            "   - Incorporate the audio tag naturally into the description\n"
            "   - Describe concisely in 10-15 words\n"
            "   - Duration: 2-6 seconds\n\n"
            f"CRITICAL: Keep all descriptions under {MAX_DESCRIPTION_LENGTH} characters.\n\n"
            "Format: 'Description naturally incorporating the audio tag. Duration: X seconds.'\n\n"
            "Examples of Radio Stabs, Stingers (Sound Effects):\n"
            "- '%news alert%': 'Sharp tones signal breaking news alert. Duration: 1 second.'\n"
            "- '%record scratch%': 'Abrupt vinyl record scratch interrupts music. Duration: 1 second.'\n"
            "- '%laser zap%': 'Futuristic laser zap effect for sci-fi segment. Duration: 2 seconds.'\n"
            "- '%crowd cheer%': 'Enthusiastic crowd cheer erupts suddenly. Duration: 3 seconds.'\n"
            "- '%cash register%': 'Classic ka-ching of cash register. Duration: 1 second.'\n\n"
            "Examples of In-Studio Activity (Environmental Noise):\n"
            "- '%chair squeaking%': 'Leather chair squeaks as someone shifts position in radio studio. Duration: 2 seconds.'\n"
            "- '%typing on keyboard%': 'Rhythmic typing on computer keyboard echoes in quiet studio space. Duration: 3 seconds.'\n"
            "- '%coffee sipping%': 'Quietly sipping hot coffee while reading the news script. Duration: 2 seconds.'\n"
            "- 'hair brushing%': 'Hair subtly brushing against microphone. Duration: 2 seconds.'\n"
            "- '%paper rustling%': 'Gentle rustling of script papers being organized before broadcast. Duration: 4 seconds.'\n"
            "- '%phone vibrating%': 'Muffled vibration of silenced phone on studio desk. Duration: 3 seconds.'"
        )

        audio_description = await self._execute_gpt_stream(
            model=self.config['audio_model'],
            max_tokens=self.config['audio_tokens'],
            temperature=self.config['audio_temperature'],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": audio_tag}
            ]
        )

        audio_description = audio_description.strip() if audio_description else ""
        if audio_description:
            if len(audio_description) > MAX_DESCRIPTION_LENGTH:
                audio_description = audio_description[:MAX_DESCRIPTION_LENGTH - 3] + '...'
            log_service.info(
                f"Audio Effects: Input audio tag: '{audio_tag}', Generated description: '{audio_description}'")
            duration_match = re.search(r'Duration: (\d+(?:\.\d+)?) seconds?\.', audio_description)
            if duration_match:
                duration_seconds = float(duration_match.group(1))
                audio_description = re.sub(r'\s*Duration: \d+(?:\.\d+)? seconds?\.', '', audio_description).strip()
            else:
                duration_seconds = None
                log_service.warning(
                    f"Audio Effects: Warning: No duration found in description for audio tag: {audio_tag}")
            return audio_tag, audio_description, duration_seconds
        else:
            log_service.info(f"Audio Effects: No description generated for audio tag: {audio_tag}")
            return None, None, None