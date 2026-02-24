import re
from services import log_service

def filter_meta_tags_for_gpt_prompt_cleaning(text):
    pattern = re.compile(
        r'\s*\*[^*]+\*\s*'
        r'|\s*%[^%]+%\s*'
        r'|\s*\$[^$]+\$\s*'
        r'|\s*@\d+@\s*'
        r'|\s*&\d+(?:\.\d+)?&\s*'
    )
    text = pattern.sub(' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def filter_response_by_role(text: str, role: str) -> str:
    if not isinstance(text, str):
        text = str(text)

    role_rules = {
        'dj_interactive': {
            'allowed_tags': ['BROADCAST', 'TXT', 'SHAQUILLE', 'TERRY', 'INTERNAL DIALOGUE', 'IMPULSE'],
            'forbidden_tags': ['HAL11000'],
            'description': 'Interactive DJ'
        },
        'dj_onboarding': {
            'allowed_tags': ['BROADCAST', 'TXT', 'SHAQUILLE', 'TERRY', 'INTERNAL DIALOGUE'],
            'forbidden_tags': ['HAL11000'],
            'description': 'Onboarding DJ'
        },
        'dj_announcements': {
            'allowed_tags': ['BROADCAST', 'TXT', 'SHAQUILLE', 'TERRY'],
            'forbidden_tags': ['HAL11000', 'INTERNAL DIALOGUE'],
            'description': 'Announcements DJ'
        },
        'dj_content': {
            'allowed_tags': ['BROADCAST', 'TXT', 'SHAQUILLE', 'TERRY'],
            'forbidden_tags': ['HAL11000', 'INTERNAL DIALOGUE'],
            'description': 'Content DJ'
        },
        'command': {
            'allowed_tags': [],
            'forbidden_tags': ['BROADCAST', 'TXT', 'SHAQUILLE', 'TERRY', 'INTERNAL DIALOGUE', 'IMPULSE', 'HAL11000'],
            'description': 'Command extraction'
        }
    }

    if role not in role_rules:
        log_service.warning(f"Filter: Unknown role '{role}', skipping role-specific filtering")
        return text

    rules = role_rules[role]

    log_service.filter(f"[TAG FILTER] Checking {rules['description']} output for forbidden blocks")
    log_service.filter(f"[TAG FILTER] Input ({len(text)} chars):\n{text[:200]}{'...' if len(text) > 200 else ''}")

    tag_pattern = r'\[([A-Z][A-Z\s0-9]*)\]'
    parts = re.split(tag_pattern, text)

    filtered_parts = []
    removed_count = 0

    i = 0
    while i < len(parts):
        if i == 0:
            if parts[i].strip():
                filtered_parts.append(parts[i])
            i += 1
        elif i + 1 < len(parts):
            tag = parts[i]
            content = parts[i + 1]

            if tag in rules['forbidden_tags']:
                log_service.filter(f"[TAG FILTER] ✗ Removed forbidden [{tag}] block: {content[:50]}...")
                removed_count += 1
            else:
                filtered_parts.append(f"[{tag}]")
                filtered_parts.append(content)
            i += 2
        else:
            i += 1

    filtered_text = ''.join(filtered_parts).strip()

    if removed_count > 0:
        log_service.filter(f"[TAG FILTER] Output ({len(filtered_text)} chars): Removed {removed_count} forbidden block(s)")
    else:
        log_service.filter("[TAG FILTER] No forbidden blocks found")

    return filtered_text

def remove_consecutive_duplicates(text: str) -> str:
    lines = text.split('\n')
    if len(lines) <= 1:
        return text

    deduplicated = []
    last_line = None
    removed_count = 0

    for line in lines:
        stripped = line.strip()
        if stripped != last_line or not stripped:
            deduplicated.append(line)
            last_line = stripped
        else:
            removed_count += 1
            log_service.filter(f"[DEDUP] Removed duplicate line: {stripped[:60]}{'...' if len(stripped) > 60 else ''}")

    if removed_count > 0:
        log_service.filter(f"[DEDUP] Removed {removed_count} consecutive duplicate line(s)")

    return '\n'.join(deduplicated)

def clean_gpt_output(text, role='dj_content'):
    if not isinstance(text, str):
        log_service.debug(f"Cleanup: Input was not a string, converting from {type(text)}")
        text = str(text)

    non_ascii = set(char for char in text if ord(char) >= 128)
    if non_ascii:
        log_service.filter(f"[CHAR CLEANUP] Found {len(non_ascii)} non-ASCII characters: {list(non_ascii)[:10]}")

    text = text.encode('ascii', 'ignore').decode('ascii')

    strange_chars = [(i, char, ord(char)) for i, char in enumerate(text)
                     if (ord(char) < 32 and char != '\n') or ord(char) > 126]
    if strange_chars:
        log_service.filter(f"[CHAR CLEANUP] Found {len(strange_chars)} unusual characters")
        for pos, char, code in strange_chars[:5]:
            log_service.filter(f"[CHAR CLEANUP] Position {pos}: '{char}' (Unicode {code})")

    text = ''.join(char for char in text if ord(char) >= 32 or char == '\n')

    text = remove_consecutive_duplicates(text)

    original_text = text
    removed_parts = []

    def remove_char_counts(text):
        return re.sub(r'\s*\([^)]*\)\s*', ' ', text)

    def handle_mismatched_tags(text):
        pattern = r'(' + '|'.join([
            r'\*[^*$%@&\s][^*$%@&]*[$%@&]',
            r'\$[^*$%@&\s][^*$%@&]*[*%@&]',
            r'%[^*$%@&\s][^*$%@&]*[*$@&]',
            r'@[^*$%@&\s][^*$%@&]*[*$%&]',
            r'&[^*$%@&\s][^*$%@&]*[*$%@]'
        ]) + r')'

        def replacer(match):
            mismatched = match.group(0)
            log_service.filter(f"[META CLEANUP] ✗ Removed mismatched tag: {mismatched}")
            removed_parts.append(f"Removed mismatched tag: {mismatched}")
            return ' '

        return re.sub(pattern, replacer, text)

    def reduce_double_tags(text):
        def replacer(match, tag_type):
            log_service.filter(f"[META CLEANUP] ✗ Reduced double {tag_type} tags: {match.group(0)} -> {tag_type}")
            removed_parts.append(f"Reduced double {tag_type} tags: {match.group(0)} -> {tag_type}")
            return tag_type

        text = re.sub(r'\*\*', lambda m: replacer(m, '*'), text)
        text = re.sub(r'%%', lambda m: replacer(m, '%'), text)
        text = re.sub(r'@@', lambda m: replacer(m, '@'), text)
        text = re.sub(r'&&', lambda m: replacer(m, '&'), text)
        text = re.sub(r'\$\$', lambda m: replacer(m, '$'), text)
        return text

    def keep_valid_tags(text):
        valid_tags = [
            'BROADCAST', 'TXT', 'TERRY', 'SHAQUILLE', 'INTERNAL DIALOGUE', 'IMPULSE'
        ]
        pattern = r'\[(' + '|'.join(valid_tags) + r')\]|' + r'\[(.*?)\](.*?)(?=\[|$)'

        def replacer(match):
            if match.group(1):
                return match.group(0)
            else:
                invalid_content = match.group(0)
                log_service.filter(f"[META CLEANUP] ✗ Removed invalid tag section: {invalid_content[:50]}{'...' if len(invalid_content) > 50 else ''}")
                removed_parts.append(f"Removed invalid tag section: {invalid_content}")
                return ' '

        return re.sub(pattern, replacer, text)

    if role != 'command':
        log_service.filter(f"[META CLEANUP] Starting cleanup for role '{role}'")
        log_service.filter(f"[META CLEANUP] Input ({len(original_text)} chars):\n{original_text[:200]}{'...' if len(original_text) > 200 else ''}")

        text = remove_char_counts(text)
        text = handle_mismatched_tags(text)
        text = reduce_double_tags(text)
        text = keep_valid_tags(text)
        text = re.sub(r'\s+', ' ', text).strip()

        if removed_parts:
            log_service.filter(f"[META CLEANUP] Output ({len(text)} chars): Made {len(removed_parts)} change(s)")
        else:
            log_service.filter("[META CLEANUP] No changes needed - text is clean")
    else:
        text = re.sub(r'[ \t]+', ' ', text).strip()
        log_service.filter("[META CLEANUP] Skipped for command role (only normalized whitespace)")

    text = filter_response_by_role(text, role)

    return text