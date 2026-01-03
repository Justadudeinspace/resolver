import logging
import re
from typing import List, Optional

from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPTS = {
    "stabilize": (
        "You are The Resolver. Provide exactly 3 short response options (1-3 sentences each) "
        "to help users de-escalate conversations.\n\n"
        "RULES:\n"
        "- Output EXACTLY 3 options, labeled \"A.\", \"B.\", \"C.\"\n"
        "- Each option must be 1-3 sentences\n"
        "- Tone: calm, empathetic, non-defensive\n"
        "- Goal: reduce tension, find common ground, prevent escalation\n"
        "- Never include threats, manipulation, or aggression\n"
        "- Keep language simple and human\n\n"
        "Format your response exactly like:\n"
        "A. [First option text]\nB. [Second option text]\nC. [Third option text]"
    ),
    "clarify": (
        "You are The Resolver. Provide exactly 3 short response options (1-3 sentences each) "
        "to help users set clear boundaries.\n\n"
        "RULES:\n"
        "- Output EXACTLY 3 options, labeled \"A.\", \"B.\", \"C.\"\n"
        "- Each option must be 1-3 sentences\n"
        "- Tone: firm, respectful, direct\n"
        "- Goal: clarify position, set boundaries, prevent misunderstandings\n"
        "- Never include threats, manipulation, or aggression\n"
        "- Keep language simple and human\n\n"
        "Format your response exactly like:\n"
        "A. [First option text]\nB. [Second option text]\nC. [Third option text]"
    ),
    "close": (
        "You are The Resolver. Provide exactly 3 short response options (1-3 sentences each) "
        "to help users end conversations cleanly.\n\n"
        "RULES:\n"
        "- Output EXACTLY 3 options, labeled \"A.\", \"B.\", \"C.\"\n"
        "- Each option must be 1-3 sentences\n"
        "- Tone: decisive, composed, final\n"
        "- Goal: provide closure, end conversation, leave no loose ends\n"
        "- Never include threats, manipulation, or aggression\n"
        "- Keep language simple and human\n\n"
        "Format your response exactly like:\n"
        "A. [First option text]\nB. [Second option text]\nC. [Third option text]"
    ),
}

MODIFIER_HINTS = {
    "softer": "Make the responses more empathetic, gentle, and understanding.",
    "firmer": "Make the responses more assertive, direct, and boundary-setting.",
    "shorter": "Make the responses more concise and to-the-point.",
}


class LLMClient:
    def __init__(self) -> None:
        self.api_key = settings.openai_api_key
        self.use_openai = settings.use_llm
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature

        if self.use_openai:
            self.client = AsyncOpenAI(api_key=self.api_key, timeout=12.0)
            logger.info("LLM client initialized with OpenAI")
        else:
            self.client = None
            logger.info("LLM client using template responses")

    async def generate_responses(
        self, goal: str, user_text: str, modifier: Optional[str] = None
    ) -> List[str]:
        """Generate three response options based on goal and modifier."""
        if not self.use_openai:
            return self._generate_template_responses(goal, modifier)

        try:
            prompt = self._build_prompt(user_text, modifier)
            system_prompt = SYSTEM_PROMPTS.get(goal, SYSTEM_PROMPTS["stabilize"])

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=350,
            )

            content = response.choices[0].message.content or ""
            parsed = self._parse_responses_robust(content)
            return parsed
        except Exception as exc:
            logger.warning("LLM generation failed; using fallback. Error: %s", exc)
            return self._generate_template_responses(goal, modifier)

    def _build_prompt(self, user_text: str, modifier: Optional[str] = None) -> str:
        prompt = f"Generate 3 response options for this situation:\n\n{user_text[:1000]}"

        if modifier:
            prompt += f"\n\nAdditional instruction: {MODIFIER_HINTS.get(modifier, modifier)}"

        return prompt

    def _parse_responses_robust(self, content: str) -> List[str]:
        content = content.strip()
        responses: List[str] = []
        lines = content.split("\n")

        current_section = None
        current_text: List[str] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if re.match(r"^A[\.:\)]", line):
                if current_section is not None and current_text:
                    responses.append(" ".join(current_text).strip())
                current_section = "A"
                cleaned_line = re.sub(r"^A[\.:\)]\s*", "", line)
                current_text = [cleaned_line]
            elif re.match(r"^B[\.:\)]", line):
                if current_section is not None and current_text:
                    responses.append(" ".join(current_text).strip())
                current_section = "B"
                cleaned_line = re.sub(r"^B[\.:\)]\s*", "", line)
                current_text = [cleaned_line]
            elif re.match(r"^C[\.:\)]", line):
                if current_section is not None and current_text:
                    responses.append(" ".join(current_text).strip())
                current_section = "C"
                cleaned_line = re.sub(r"^C[\.:\)]\s*", "", line)
                current_text = [cleaned_line]
            elif current_section is not None:
                current_text.append(line)

        if current_section is not None and current_text:
            responses.append(" ".join(current_text).strip())

        if len(responses) != 3:
            sections = re.split(r"\n\s*(?:\d+[\.:\)]|\-|\*|•)\s*", content)
            if len(sections) >= 4:
                responses = [s.strip() for s in sections[1:4] if s.strip()]

        if len(responses) != 3:
            words = content.split()
            if not words:
                return self._generate_template_responses("stabilize", None)
            chunk_size = max(1, len(words) // 3)
            responses = []
            for i in range(3):
                start = i * chunk_size
                end = (i + 1) * chunk_size if i < 2 else len(words)
                responses.append(" ".join(words[start:end]).strip())

        while len(responses) < 3:
            responses.append("Let me try a different approach.")

        clean_responses = []
        for resp in responses[:3]:
            resp = re.sub(r"^[A-C][\.:\)]\s*", "", resp).strip()
            clean_responses.append(resp)

        return clean_responses[:3]

    def _generate_template_responses(self, goal: str, modifier: Optional[str]) -> List[str]:
        modifier_text = ""
        if modifier:
            modifier_map = {
                "softer": " (more empathetic)",
                "firmer": " (more direct)",
                "shorter": " (more concise)",
            }
            modifier_text = modifier_map.get(modifier, "")

        goal_templates = {
            "stabilize": [
                f"I can see this is important to you{modifier_text}. Let's slow down and make sure we hear each other.",
                f"I want to understand where you're coming from{modifier_text}. Can we take a moment to reset?",
                f"Thanks for sharing this{modifier_text}. I'm listening and want to respond thoughtfully.",
            ],
            "clarify": [
                f"I want to be clear about my position{modifier_text}: I hear you, and here's where I stand.",
                f"To avoid confusion{modifier_text}, I need to set a boundary about what I can do here.",
                f"I respect your point{modifier_text}, and I also need to be direct about my limits.",
            ],
            "close": [
                f"I think we've covered what we can for now{modifier_text}. I'm going to end this here.",
                f"I appreciate the discussion{modifier_text}, but I need to close this conversation now.",
                f"Let's pause this here{modifier_text}. We can revisit if needed later.",
            ],
        }

        return goal_templates.get(goal, goal_templates["stabilize"])


llm_client = LLMClient()
