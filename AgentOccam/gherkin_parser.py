"""
Gherkin Parser for AgentOccam
Parses Gherkin-style scenarios and converts them to agent objectives
"""
import re
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from AgentOccam.logger import logger


@dataclass
class GherkinScenario:
    """Represents a parsed Gherkin scenario"""
    feature: str
    scenario: str
    given: List[str]
    when: List[str]
    then: List[str]
    
    def to_natural_language(self) -> str:
        """Convert Gherkin scenario to a tester-oriented task brief."""
        lines = [
            "Execute the following Gherkin test case as a tester.",
            "",
            "Interpretation rules:",
            "- GIVEN describes the initial/current state or preconditions. Treat it as context to confirm, not as the main action sequence unless setup is missing.",
            "- WHEN describes the user actions you must perform, in order. Do not skip, rewrite, or replace these steps unless the UI requires an equivalent interaction.",
            "- THEN describes the final expected result, screen, message, data, or state you must verify before stopping.",
            "- Stop only after the THEN expectations are visibly satisfied, or after reporting the observed failure.",
        ]

        if self.feature:
            lines.extend(["", f"FEATURE: {self.feature}"])
        if self.scenario:
            lines.append(f"SCENARIO: {self.scenario}")

        if self.given:
            lines.append("GIVEN:")
            lines.extend([f"- {step}" for step in self.given])
        if self.when:
            lines.append("WHEN:")
            lines.extend([f"- {step}" for step in self.when])
        if self.then:
            lines.append("THEN:")
            lines.extend([f"- {step}" for step in self.then])

        objective = "\n".join(lines)
        logger.debug(f"Gherkin scenario converted to tester task brief: {objective}")

        return objective
    
    def get_acceptance_criteria(self) -> List[str]:
        """Extract acceptance criteria from Then clauses"""
        return self.then.copy()
    
    def to_dict(self) -> Dict:
        """Convert to dictionary format"""
        return {
            "feature": self.feature,
            "scenario": self.scenario,
            "given": self.given,
            "when": self.when,
            "then": self.then,
            "objective": self.to_natural_language(),
            "acceptance_criteria": self.get_acceptance_criteria()
        }


class GherkinParser:
    """Parser for Gherkin-style scenarios"""

    _WITH_VALUE_RE = re.compile(r"\bwith\s+['\"]", re.IGNORECASE)
    _FILL_STEP_RE = re.compile(
        r"^\s*I\s+(?:fill\s+in|enter)\s+(?P<label>.+?)(?:\s+(?:with|as)\s+['\"].*)?\s*$",
        re.IGNORECASE,
    )

    @staticmethod
    def _normalize_field_reference(label: str) -> str:
        text = re.sub(r"\s+", " ", str(label or "")).strip().strip("'\"")
        text = re.sub(r"^\s*(?:the|a|an)\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(
            r"\s+(?:field|textbox|input|box)\s*$",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return text.strip(" :")

    @classmethod
    def _extract_isp_entries(cls, isp_test_case: Dict[str, Any]) -> List[tuple[str, str]]:
        entries: List[tuple[str, str]] = []
        if not isinstance(isp_test_case, dict):
            return entries

        for key, payload in isp_test_case.items():
            if str(key).startswith("_"):
                continue
            if isinstance(payload, dict):
                value = str(payload.get("value", ""))
            else:
                value = str(payload)
            entries.append((cls._normalize_field_reference(key).lower(), value))
        return entries

    @classmethod
    def _find_matching_isp_value(
        cls,
        step_field: str,
        entries: List[tuple[str, str]],
    ) -> tuple[Optional[str], Optional[int]]:
        exact_match: Optional[tuple[str, int]] = None
        fuzzy_matches: List[tuple[int, int, str]] = []

        for idx, (label, value) in enumerate(entries):
            if not label:
                continue
            if label == step_field:
                exact_match = (value, idx)
                break
            if step_field and (label in step_field or step_field in label):
                fuzzy_matches.append((abs(len(label) - len(step_field)), idx, value))

        if exact_match is not None:
            return exact_match[0], exact_match[1]
        if fuzzy_matches:
            _, idx, value = min(fuzzy_matches, key=lambda item: (item[0], item[1]))
            return value, idx
        return None, None

    @classmethod
    def _inject_isp_values_into_when(
        cls,
        when_steps: List[str],
        isp_test_case: Dict[str, Any],
    ) -> List[str]:
        """Inject ISP values into fill-in steps by field label, then by order."""
        entries = cls._extract_isp_entries(isp_test_case)
        values = [value for _, value in entries]
        if not values:
            return when_steps

        injected: List[str] = []
        value_idx = 0

        for raw_step in when_steps:
            step = str(raw_step)
            fill_match = cls._FILL_STEP_RE.match(step)
            is_fill_step = fill_match is not None

            if is_fill_step:
                matched_value: Optional[str] = None
                matched_idx: Optional[int] = None
                step_field = cls._normalize_field_reference(fill_match.group("label")).lower()
                if step_field:
                    matched_value, matched_idx = cls._find_matching_isp_value(step_field, entries)

                if matched_value is None and value_idx < len(values):
                    matched_value = values[value_idx]
                    matched_idx = value_idx

                if matched_value is not None:
                    if not cls._WITH_VALUE_RE.search(step):
                        step = f'{step} with "{matched_value}"'
                    if matched_idx is not None:
                        value_idx = max(value_idx, matched_idx + 1)

            injected.append(step)

        return injected
    
    @staticmethod
    def parse(gherkin_text: str) -> GherkinScenario:
        """
        Parse Gherkin scenario text into structured format
        
        Args:
            gherkin_text: Gherkin scenario as string
            
        Returns:
            GherkinScenario object
            
        Example:
            ```
            Feature: Search functionality
            Scenario: Search for Python programming
              Given I am on Google homepage
              When I search for "Python programming"
              And I click on the first result
              Then I should see Python documentation
            ```
        """
        lines = [line.strip() for line in gherkin_text.strip().split('\n') if line.strip()]
        
        feature = ""
        scenario = ""
        given = []
        when = []
        then = []
        
        current_section = None
        
        for line in lines:
            # Parse Feature
            if line.startswith("Feature:"):
                feature = line.replace("Feature:", "").strip()
                continue
            
            # Parse Scenario
            if line.startswith("Scenario:"):
                scenario = line.replace("Scenario:", "").strip()
                continue
            
            # Parse Given
            if line.startswith("Given "):
                current_section = "given"
                given.append(line.replace("Given ", "").strip())
                continue
            
            # Parse When
            if line.startswith("When "):
                current_section = "when"
                when.append(line.replace("When ", "").strip())
                continue
            
            # Parse Then
            if line.startswith("Then "):
                current_section = "then"
                then.append(line.replace("Then ", "").strip())
                continue
            
            # Parse And/But (continues previous section)
            if line.startswith("And ") or line.startswith("But "):
                cleaned_line = re.sub(r'^(And|But) ', '', line).strip()
                if current_section == "given":
                    given.append(cleaned_line)
                elif current_section == "when":
                    when.append(cleaned_line)
                elif current_section == "then":
                    then.append(cleaned_line)
                continue
        
        return GherkinScenario(
            feature=feature,
            scenario=scenario,
            given=given,
            when=when,
            then=then
        )
    
    @staticmethod
    def parse_from_dict(data: Dict) -> GherkinScenario:
        """
        Parse from dictionary format (for JSON task configs)
        
        Args:
            data: Dict with 'gherkin' field containing scenario text or dict
                  OR dict with structured fields (feature, scenario, given, when, then)
        
        Returns:
            GherkinScenario object
        """
        if "gherkin" in data:
            gherkin_content = data["gherkin"]
            # Check if gherkin is a string or dict
            if isinstance(gherkin_content, str):
                # Parse from text
                scenario = GherkinParser.parse(gherkin_content)
                scenario.when = GherkinParser._inject_isp_values_into_when(
                    scenario.when,
                    data.get("isp_test_case", {}),
                )
                return scenario
            elif isinstance(gherkin_content, dict):
                # Parse from nested dict structure
                given = gherkin_content.get("given", [])
                when = gherkin_content.get("when", [])
                then = gherkin_content.get("then", [])

                given_list = given if isinstance(given, list) else [given]
                when_list = when if isinstance(when, list) else [when]
                then_list = then if isinstance(then, list) else [then]

                when_list = GherkinParser._inject_isp_values_into_when(
                    [str(step) for step in when_list],
                    data.get("isp_test_case", {}),
                )

                return GherkinScenario(
                    feature=gherkin_content.get("feature", ""),
                    scenario=gherkin_content.get("scenario", ""),
                    given=[str(step) for step in given_list],
                    when=when_list,
                    then=[str(step) for step in then_list],
                )
        elif all(k in data for k in ["feature", "scenario", "given", "when", "then"]):
            # Parse from structured format (direct fields)
            given = data.get("given", [])
            when = data.get("when", [])
            then = data.get("then", [])

            given_list = given if isinstance(given, list) else [given]
            when_list = when if isinstance(when, list) else [when]
            then_list = then if isinstance(then, list) else [then]

            when_list = GherkinParser._inject_isp_values_into_when(
                [str(step) for step in when_list],
                data.get("isp_test_case", {}),
            )

            return GherkinScenario(
                feature=data.get("feature", ""),
                scenario=data.get("scenario", ""),
                given=[str(step) for step in given_list],
                when=when_list,
                then=[str(step) for step in then_list],
            )
        else:
            raise ValueError("Invalid Gherkin data format. Must contain 'gherkin' field (string or dict) or structured fields (feature, scenario, given, when, then).")
    
    @staticmethod
    def validate(gherkin_text: str) -> bool:
        """Validate Gherkin scenario syntax"""
        try:
            scenario = GherkinParser.parse(gherkin_text)
            # Must have at least When and Then
            return len(scenario.when) > 0 and len(scenario.then) > 0
        except Exception:
            return False


# Convenience functions
def parse_gherkin(text_or_dict) -> GherkinScenario:
    """
    Parse Gherkin from text or dict
    
    Args:
        text_or_dict: Either Gherkin text string or dict with gherkin field
    
    Returns:
        GherkinScenario object
    """
    if isinstance(text_or_dict, str):
        return GherkinParser.parse(text_or_dict)
    elif isinstance(text_or_dict, dict):
        return GherkinParser.parse_from_dict(text_or_dict)
    else:
        raise ValueError("Input must be string or dict")


def gherkin_to_objective(text_or_dict) -> str:
    """
    Convert Gherkin scenario to natural language objective
    
    Args:
        text_or_dict: Either Gherkin text string or dict with gherkin field
    
    Returns:
        Natural language objective string
    """
    scenario = parse_gherkin(text_or_dict)
    return scenario.to_natural_language()
