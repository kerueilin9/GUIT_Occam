"""
Prompt-building utilities for ISP (Input Space Partitioning) value generation.
"""

ISP_SYSTEM_PROMPT = """You are a software testing expert specialising in \
Input Space Partitioning (ISP).
Your role is to generate diverse test inputs that cover different equivalence \
classes and boundary conditions for web form fields, helping identify potential \
bugs and edge cases in web applications."""


def build_isp_generation_prompt(field_meta, n: int) -> str:
    """Return a prompt that asks the LLM to produce *n* ISP partitions.

    Parameters
    ----------
    field_meta : FieldMetadata
        Metadata about the target field (label, type, required, context, …).
    n : int
        Number of partition values to generate (excluding the original value,
        which the caller already adds separately).
    """
    field_info = (
        f"Field Information:\n"
        f"- Label: {field_meta.label or '(unknown)'}\n"
        f"- Input Type: {field_meta.input_type}\n"
        f"- Required: {field_meta.required}\n"
        f"- Original Value Used by Agent: {repr(field_meta.original_value)}\n"
        f"- Surrounding Context (from accessibility tree):\n"
        f"  {field_meta.surrounding_context[:300]}"
    )

    prompt = f"""{ISP_SYSTEM_PROMPT}

{field_info}

Using Input Space Partitioning (ISP) principles, generate exactly {n} test \
input values for this field.
The values should cover different equivalence classes, for example:
1. A **valid** normal input (different length or wording from the original)
2. **Boundary** values (empty string, minimum/maximum length, edge-case lengths)
3. **Invalid** inputs (wrong format, special characters, SQL/XSS injection)
4. **Empty** input (empty string — useful for required-field validation)

Rules:
- Do NOT include the original value "{field_meta.original_value}" in your output.
- Return ONLY a valid JSON array — no prose, no markdown fences.
- Each element must have exactly three string keys: "value", "category", "description".
- "category" must be one of: "valid", "boundary", "invalid", "empty".
- All JSON string values must be properly escaped.

Example output (5 items):
[
  {{"value": "Sample Title", "category": "valid", "description": "Normal short text"}},
  {{"value": "", "category": "empty", "description": "Empty string — tests required-field validation"}},
  {{"value": "a", "category": "boundary", "description": "Single character — minimum length boundary"}},
  {{"value": "{('x' * 300)}", "category": "boundary", "description": "300-char string — exceeds typical field length"}},
  {{"value": "<script>alert(1)</script>", "category": "invalid", "description": "XSS injection attempt"}}
]

Generate exactly {n} partition values for this specific field.  \
Return only the JSON array:"""

    return prompt


def build_isp_combination_prompt(
    field_partitions: dict,
    gherkin_context: dict,
    max_combinations: int,
) -> str:
    """Build a prompt that asks the LLM to select representative ISP combinations.

    Parameters
    ----------
    field_partitions : dict
        ``{field_label: [ISPPartition, ...]}`` — all generated partitions.
    gherkin_context : dict
        The ``gherkin`` block from the task config (feature / scenario / when).
    max_combinations : int
        Suggested upper bound on how many combinations to return.
    """
    scenario_text = ""
    if gherkin_context:
        scenario_text = (
            f"Task Scenario: {gherkin_context.get('scenario', '')}\n"
            f"Steps: {'; '.join(gherkin_context.get('when', []))}\n"
        )

    fields_text = ""
    for label, partitions in field_partitions.items():
        fields_text += f'\nField "{label}":\n'
        for i, p in enumerate(partitions):
            fields_text += (
                f"  [{i}] value={repr(p.value)}  "
                f"category={p.category}  desc={p.description}\n"
            )

    prompt = f"""{ISP_SYSTEM_PROMPT}

{scenario_text}
You are generating ISP (Input Space Partitioning) test cases for a web form.
Below are the input fields and their candidate test values:
{fields_text}

Select a representative set of test combinations (at most {max_combinations}).
Good coverage includes: all-valid runs, one-field-empty/invalid while others are valid, boundary conditions.
Avoid redundant combinations that test the same behaviour.

Return ONLY a valid JSON array — no prose, no markdown fences.
Each element is an object mapping field label to a partition object.
Each partition object must have keys: "value", "category", "description".

Example (2 fields, 3 combinations):
[
  {{"title": {{"value": "My Post", "category": "valid", "description": "Normal input"}}, "content": {{"value": "Hello world", "category": "valid", "description": "Normal body"}}}},
  {{"title": {{"value": "", "category": "empty", "description": "Empty — required validation"}}, "content": {{"value": "Hello world", "category": "valid", "description": "Normal body"}}}},
  {{"title": {{"value": "My Post", "category": "valid", "description": "Normal input"}}, "content": {{"value": "<script>alert(1)</script>", "category": "invalid", "description": "XSS attempt"}}}}
]

Return the JSON array of up to {max_combinations} combinations:"""

    return prompt
