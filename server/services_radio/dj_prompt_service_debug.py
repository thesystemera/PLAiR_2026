"""Prompt debug files: each prompt and response written to the prompt debug folder for inspection."""
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from services_radio.dj_prompt_helper_service import (
    assemble_prompt
)
from services_radio import talk_clock
from services import log_service
from services.llm_router import LLM_ANNOUNCE
from config.settings import settings
from services_radio.dj_prompt_service_configs import gpt_error_handler


class PromptDebug:
    def _save_prompt_debug(
            self,
            gpt_type: str,
            user_input: str,
            required_nodes: List[str],
            dynamic_nodes: List[str],
            all_nodes: List[str],
            system_prompt: str,
            context_data: Dict[str, str]
    ):
        if not settings.PROMPT_DEBUG_ENABLED:
            return None

        try:
            debug_dir = Path(settings.PROMPT_DEBUG_DIR)
            debug_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]
            filename = f"{gpt_type}_{timestamp}.json"
            filepath = debug_dir / filename

            estimated_tokens = len(system_prompt) // 4

            debug_data = {
                "gpt_type": gpt_type,
                "timestamp": timestamp,
                "user_input": user_input,
                "required_nodes": required_nodes,
                "dynamic_nodes": dynamic_nodes,
                "all_nodes": all_nodes,
                "node_count_total": len(all_nodes),
                "node_count_required": len(required_nodes),
                "node_count_dynamic": len(dynamic_nodes),
                "system_prompt": system_prompt,
                "prompt_length_chars": len(system_prompt),
                "estimated_tokens": estimated_tokens,
                "node_outputs": {
                    node: {
                        "content": context_data.get(node, ""),
                        "length_chars": len(context_data.get(node, ""))
                    }
                    for node in all_nodes
                }
            }

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(debug_data, f, indent=2, ensure_ascii=False)

            txt_filename = f"{gpt_type}_{timestamp}.txt"
            txt_filepath = debug_dir / txt_filename

            with open(txt_filepath, 'w', encoding='utf-8') as f:
                f.write("=" * 80 + "\n")
                f.write(f"PROMPT DEBUG - {gpt_type.upper()} - {timestamp}\n")
                f.write("=" * 80 + "\n\n")
                f.write(f"GPT Type: {gpt_type}\n")
                f.write(f"User Input: {user_input}\n\n")

                f.write(f"REQUIRED Nodes ({len(required_nodes)}): {', '.join(required_nodes)}\n")
                if dynamic_nodes:
                    f.write(f"DYNAMIC Nodes ({len(dynamic_nodes)}): {', '.join(dynamic_nodes)}\n")
                else:
                    f.write("DYNAMIC Nodes (0): None (static GPT function)\n")
                f.write(f"TOTAL Nodes ({len(all_nodes)})\n\n")

                f.write(f"Estimated Tokens: {estimated_tokens}\n")
                f.write(f"Prompt Length: {len(system_prompt)} chars\n\n")
                f.write("=" * 80 + "\n")
                f.write("FULL SYSTEM PROMPT\n")
                f.write("=" * 80 + "\n\n")
                f.write(system_prompt)
                f.write("\n\n")
                f.write("=" * 80 + "\n")
                f.write("NODE BREAKDOWN\n")
                f.write("=" * 80 + "\n\n")
                for i, node in enumerate(all_nodes, 1):
                    content = context_data.get(node, "")
                    node_type = "REQUIRED" if node in required_nodes else "DYNAMIC"
                    f.write(f"{i}. [{node_type}] {node} ({len(content)} chars)\n")
                    f.write("-" * 80 + "\n")
                    f.write(content)
                    f.write("\n\n")

            log_service.node_performance(f"📝 Saved {gpt_type} prompt debug: {filename} + {txt_filename}")
            return timestamp

        except Exception as e:
            log_service.error(f"Failed to save prompt debug: {e}")
            return None

    def _append_response_to_debug(self, gpt_type: str, timestamp: str, response: str):
        if not timestamp:
            return

        try:
            debug_dir = Path(settings.PROMPT_DEBUG_DIR)

            json_filepath = debug_dir / f"{gpt_type}_{timestamp}.json"
            if json_filepath.exists():
                with open(json_filepath, 'r', encoding='utf-8') as f:
                    debug_data = json.load(f)

                debug_data['gpt_response'] = response
                debug_data['response_length_chars'] = len(response) if response else 0

                with open(json_filepath, 'w', encoding='utf-8') as f:
                    json.dump(debug_data, f, indent=2, ensure_ascii=False)

            txt_filepath = debug_dir / f"{gpt_type}_{timestamp}.txt"
            if txt_filepath.exists():
                with open(txt_filepath, 'a', encoding='utf-8') as f:
                    f.write("=" * 80 + "\n")
                    f.write("GPT RESPONSE\n")
                    f.write("=" * 80 + "\n\n")
                    if response:
                        f.write(response)
                        f.write(f"\n\nResponse Length: {len(response)} chars\n")
                    else:
                        f.write("[N/A] - GPT returned None\n")
                    f.write("\n")

            log_service.node_performance(f"📝 Appended response to {gpt_type} debug: {timestamp}")

        except Exception as e:
            log_service.error(f"Failed to append response to debug: {e}")

    @gpt_error_handler
    async def gpt_dj_announcements(self, transition_duration_ms, session_dict):
        time_remaining = transition_duration_ms / 1000.0 if transition_duration_ms else 0.0

        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='announcements',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            time_remaining=time_remaining,
            transition_duration_ms=transition_duration_ms
        )

        system_prompt = assemble_prompt(context_data, final_nodes)
        log_service.gpt(f"Announcer: Announcements Prompt: {system_prompt}")

        response_text = await self._execute_gpt_and_save(
            gpt_type='announcements',
            debug_timestamp=debug_timestamp or "",
            messages=[{"role": "system", "content": system_prompt}],
            clean_role='dj_announcements',
            role=LLM_ANNOUNCE,
            validate_script=True
        )

        log_service.api(f"Announcer: Announcements Raw Response: {response_text}")

        if "[N/A]" in response_text:
            log_service.api("Announcer: Announcements response is not applicable ([N/A])")
            return None
        response_text = response_text.strip().strip('"')
        log_service.commands(
            f"{log_service.who(session_dict.get('session_id'), user_id=session_dict.get('user_id'))}: announcement | "
            f"window {time_remaining:.1f}s = {talk_clock.words_for(time_remaining, 'announcer')} words at "
            f"{talk_clock.pace('announcer'):.2f} words/s | wrote {talk_clock.spoken_words(response_text)} words")
        return response_text
