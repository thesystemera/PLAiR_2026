from typing import Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, Conversation
from services import log_service

async def generate_user_persona_and_profile(user_id: int, db: AsyncSession, ai_service) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        log_service.persona_profile(f"User {user_id} not found")
        return None, None, None

    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .where(Conversation.user_input.isnot(None))
        .order_by(Conversation.timestamp.desc())
        .limit(10)
    )
    recent_conversations = result.scalars().all()

    if recent_conversations is None or len(recent_conversations) == 0:
        log_service.persona_profile(f"No conversation history for user {user_id}")
        return None, None, None

    conversation_history = []
    for conv in reversed(list(recent_conversations)):
        conversation_history.append(f"User: {conv.user_input}")
        if conv.bot_response is not None:
            conversation_history.append(f"DJ: {conv.bot_response[:100]}...")

    conversation_history_str = "\n".join(conversation_history)

    log_service.persona_profile(f"Generating persona for user {user_id} based on {len(recent_conversations)} conversations")

    prompt = f"""Your role is to create or refine a [Listener Persona], [Listener Profile], and [Shoutout Interests] based on the conversation history below.

CRITICAL: These three sections are DISTINCT and must NOT overlap:

For the [Listener Profile], extract DEMOGRAPHIC FACTS ONLY:
- Age range, gender, location/city/region
- Family status (children, relationship status, living situation - flatting, owns home, etc.)
- Occupation, student status, or work situation
- Physical characteristics if mentioned (NOT personality traits)
- DO NOT include music preferences, personality, or interests here

For the [Listener Persona], extract PERSONALITY, CHARACTER, AND MUSICAL IDENTITY:
- Musical taste: favorite artists, genres they love/hate, music discovery patterns
- Communication style: formal/casual, humor type, vocabulary, emoji usage
- Emotional patterns: energy levels, mood tendencies, how they express feelings
- Values and priorities: what matters to them, social tendencies, lifestyle philosophy
- DO NOT include demographics - focus on WHO they are, not WHAT they are

For the [Shoutout Interests], extract DISCOVERY TOPICS for local/community content:
- Specific activities: garage sales, live music venues, food trucks, farmers markets, art shows
- Community interests: local events, announcements, sports teams, neighborhood happenings
- Hobby-related topics: photography, gaming, fitness, cooking, crafts, outdoor activities
- DO NOT include music preferences (those belong in Persona)
- Limit to 10 interests maximum, comma-separated, optimized for semantic search

Keep each section concise (75-100 tokens max for Persona/Profile). Use clear, dense sentences. No bullet points.

[Current Listener Persona]
{user.persona or 'Not yet defined'}

[Current Listener Profile]
{user.profile or 'Not yet defined'}

[Current Shoutout Interests]
{user.shoutout_interests or 'Not yet defined'}

[Recent Conversation History]
{conversation_history_str}

INSTRUCTIONS:
1. Limit Persona and Profile sections to 75-100 tokens each
2. Limit Shoutout Interests to 10 topics maximum, comma-separated
3. Prioritize recent, significant information
4. Use concise, clear sentences
5. ONLY update a section if there is genuinely NEW information not already captured in the current version
6. If a section already adequately covers the topic, respond with "No updates needed." for THAT section only
7. You can update one, two, or all three sections independently based on what new information is available
8. Format your response exactly as:

[Updated Listener Persona]
<persona text here OR "No updates needed.">

[Revised Listener Profile]
<profile text here OR "No updates needed.">

[Shoutout Interests]
<comma-separated interests here OR "No updates needed.">
"""

    try:
        response = await ai_service.call_gemini(
            prompt=prompt,
            system_instruction="You are a user profiling expert. Analyze conversations to create concise, information-dense user personas and profiles.",
            model="gemini-2.5-flash-lite",
            temperature=0.3
        )

        if not response:
            log_service.persona_profile(f"No response from Gemini for user {user_id}")
            return None, None, None

        log_service.persona_profile(f"Gemini response for user {user_id}: {response[:200]}...")

        updated_persona = ""
        updated_profile = ""
        updated_interests = ""
        current_section = None

        for line in response.split('\n'):
            line_stripped = line.strip()
            if line_stripped == "[Updated Listener Persona]":
                current_section = "persona"
            elif line_stripped == "[Revised Listener Profile]":
                current_section = "profile"
            elif line_stripped == "[Shoutout Interests]":
                current_section = "interests"
            elif current_section and line_stripped:
                if current_section == "persona":
                    updated_persona += line + "\n"
                elif current_section == "profile":
                    updated_profile += line + "\n"
                elif current_section == "interests":
                    updated_interests += line + "\n"

        updated_persona = updated_persona.strip()
        updated_profile = updated_profile.strip()
        updated_interests = updated_interests.strip()

        if updated_persona == "No updates needed." or updated_profile == "No updates needed.":
            log_service.persona_profile(f"No significant updates for user {user_id}")
            return None, None, None

        return updated_persona, updated_profile, updated_interests

    except Exception as e:
        log_service.error(f"Error generating persona for user {user_id}: {e}")
        return None, None, None

async def update_user_persona_if_needed(user_id: int, db: AsyncSession, ai_service):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return

    user.engagements_since_last_update += 1  # type: ignore
    await db.commit()

    log_service.persona_profile(f"User {user_id} engagements: {user.engagements_since_last_update}/5")

    if user.engagements_since_last_update >= 5:  # type: ignore
        log_service.persona_profile(f"Threshold reached, updating persona for user {user_id}")

        updated_persona, updated_profile, updated_interests = await generate_user_persona_and_profile(user_id, db, ai_service)

        if updated_persona and updated_persona != "No updates needed.":
            user.persona = updated_persona  # type: ignore
            log_service.persona_profile(f"Updated persona for user {user_id}")

        if updated_profile and updated_profile != "No updates needed.":
            user.profile = updated_profile  # type: ignore
            log_service.persona_profile(f"Updated profile for user {user_id}")

        if updated_interests and updated_interests != "No updates needed.":
            user.shoutout_interests = updated_interests  # type: ignore
            log_service.persona_profile(f"Updated shoutout interests for user {user_id}")

        if updated_persona or updated_profile or updated_interests:
            user.engagements_since_last_update = 0  # type: ignore
            await db.commit()
            log_service.persona_profile(f"Reset engagement counter for user {user_id}")