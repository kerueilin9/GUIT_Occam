"""Prompt-building utilities for ISP testcase generation."""

ISP_SYSTEM_PROMPT = """You are a software testing expert specialising in \
Input Space Partitioning (ISP).
Your role is to generate diverse test inputs that cover different equivalence \
classes and boundary conditions for web form fields, helping identify potential \
bugs and edge cases in web applications."""


def _format_field_info(field_name: str, field_meta) -> str:
    return (
        f'Field "{field_name}":\n'
        f"- Label: {field_meta.label or '(unknown)'}\n"
        f"- Input Type: {field_meta.input_type}\n"
        f"- Required: {field_meta.required}\n"
        f"- Original Value Used by Agent: {repr(field_meta.original_value)}\n"
        f"- Surrounding Context (from accessibility tree):\n"
        f"  {field_meta.surrounding_context[:300]}"
    )


def build_isp_testcase_generation_prompt(
    field_metas: dict,
    max_cases: int,
    gherkin_context: dict | None = None,
) -> str:
    """Return a prompt that asks the LLM to produce complete ISP testcases."""
    gherkin_context = gherkin_context or {}
    fields_info = "\n\n".join(
        _format_field_info(field_name, field_meta)
        for field_name, field_meta in field_metas.items()
    )
    field_keys = ", ".join(f'"{field_name}"' for field_name in field_metas.keys())

    scenario_parts: list[str] = []
    if gherkin_context.get("feature"):
        scenario_parts.append(f'Feature: {gherkin_context.get("feature", "")}')
    if gherkin_context.get("scenario"):
        scenario_parts.append(f'Scenario: {gherkin_context.get("scenario", "")}')
    when_steps = gherkin_context.get("when", [])
    if when_steps:
        scenario_parts.append(
            "Scenario Steps:\n" + "\n".join(f"- {step}" for step in when_steps)
        )
    scenario_text = "\n".join(scenario_parts).strip()

    prompt = f"""{ISP_SYSTEM_PROMPT}

You are generating complete ISP test cases for a single web form.
{scenario_text}

Fields to cover:
{fields_info}

Generate a compact set of distinct test cases, with at most {max_cases} test cases total.
Within that limit, include as many meaningful and distinct ISP combinations as practical.
Each test case must be a JSON object with exactly these keys:
- "name": a short descriptive name
- "expected": one of "pass" or "fail"
- "inputs": a JSON object containing exactly these field keys: {field_keys}

Rules:
- Return ONLY a valid JSON array — no prose, no markdown fences.
- The top-level JSON array must contain between 3 and {max_cases} test case objects.
- Every "inputs" object must contain exactly these keys: {field_keys}
- Keep values concrete and ready to type into the form.
- Keep each individual input value concise; use representative boundary strings, not thousands of repeated characters.
- For ordinary non-unique fields, use each field's original value as the baseline unless the testcase intentionally changes it.
- Include at least one fully valid baseline case.
- For uniqueness-sensitive fields such as email, phone, mobile number, username, employee ID, or other record-unique identifiers:
  use a fresh valid replacement value in the baseline and in almost every testcase instead of reusing the original value.
- Reserve at most one deliberate duplicate-existing testcase for those uniqueness-sensitive fields,
  where the value stays the same as the original input to test whether the system rejects duplicates.
- If the scenario is clearly about creating or adding a new record, that duplicate-existing testcase should usually have "expected": "fail".
- Include obvious cross-field dependency cases when labels imply them, such as password confirmation mismatches.
- Prefer changing one logical validation condition per testcase unless the case is intentionally testing a cross-field dependency.
- Do NOT add explanations, rationales, or extra keys.
- All JSON string values must be properly escaped.

Example output:
[
  {{
    "name": "baseline valid",
    "expected": "pass",
    "inputs": {{
      "First Name": "John",
      "Last Name": "Doe",
      "Email Address": "john.doe+isp1@example.com",
      "Password": "SecurePass!23",
      "Confirm Password": "SecurePass!23"
    }}
  }},
  {{
    "name": "duplicate existing email",
    "expected": "fail",
    "inputs": {{
      "First Name": "John",
      "Last Name": "Doe",
      "Email Address": "john.doe@example.com",
      "Password": "SecurePass!23",
      "Confirm Password": "SecurePass!23"
    }}
  }},
  {{
    "name": "password mismatch",
    "expected": "fail",
    "inputs": {{
      "First Name": "John",
      "Last Name": "Doe",
      "Email Address": "john.doe+isp2@example.com",
      "Password": "SecurePass!23",
      "Confirm Password": "MismatchPass"
    }}
  }}
]

Return only the JSON array:"""

    return prompt
