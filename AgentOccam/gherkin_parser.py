"""
Gherkin Parser for AgentOccam
Parses Gherkin-style scenarios and converts them to agent objectives
"""
import re
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class GherkinScenario:
    """Represents a parsed Gherkin scenario"""
    feature: str
    scenario: str
    given: List[str]
    when: List[str]
    then: List[str]
    
    def to_natural_language(self) -> str:
        """Convert Gherkin scenario to natural language objective for the agent"""
        parts = []
        
        # Add context from Given clauses
        if self.given:
            context = " and ".join(self.given)
            parts.append(f"Starting from {context}")
        
        # Add actions from When clauses
        if self.when:
            actions = " and then ".join(self.when)
            parts.append(f"perform the following: {actions}")
        
        # Add expected outcome from Then clauses
        if self.then:
            expectations = " and ".join(self.then)
            parts.append(f"so that {expectations}")
        
        return ", ".join(parts) + "."
    
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
                return GherkinParser.parse(gherkin_content)
            elif isinstance(gherkin_content, dict):
                # Parse from nested dict structure
                return GherkinScenario(
                    feature=gherkin_content.get("feature", ""),
                    scenario=gherkin_content.get("scenario", ""),
                    given=gherkin_content.get("given", []) if isinstance(gherkin_content.get("given"), list) else [gherkin_content.get("given", "")],
                    when=gherkin_content.get("when", []) if isinstance(gherkin_content.get("when"), list) else [gherkin_content.get("when", "")],
                    then=gherkin_content.get("then", []) if isinstance(gherkin_content.get("then"), list) else [gherkin_content.get("then", "")]
                )
        elif all(k in data for k in ["feature", "scenario", "given", "when", "then"]):
            # Parse from structured format (direct fields)
            return GherkinScenario(
                feature=data.get("feature", ""),
                scenario=data.get("scenario", ""),
                given=data.get("given", []) if isinstance(data.get("given"), list) else [data.get("given", "")],
                when=data.get("when", []) if isinstance(data.get("when"), list) else [data.get("when", "")],
                then=data.get("then", []) if isinstance(data.get("then"), list) else [data.get("then", "")]
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
