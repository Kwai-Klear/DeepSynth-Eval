PROMPTS = {}

PROMPTS["checklist_judge"] = """You are an expert evaluator.

=== Task ===
Your task is to assess whether the provided survey content meets the specific requirements listed in the criteria.

=== Input Data: Survey Content ===
{survey_content}

=== Input Data: Criteria ===
{criteria_text}

=== Evaluation Options ===
For each requirement listed in the **Criteria**, select exactly one of the following statuses:
- `mentioned_correct`: The requirement is present and accurate.
- `not_mentioned`: The requirement is absent.
- `mentioned_incorrect`: The requirement is present but incorrect or flawed.

=== Instructions ===
1. **Analyze Step-by-Step**: Read the survey and compare it against each requirement in the criteria list one by one.
2. **Determine Status**: Decide the appropriate status string for each requirement.
3. **Verify Length**: Ensure the final list contains exactly {req_cnt} items.
4. **Format Output**: Output the result as a JSON list of strings.

=== Output Format ===
Only return a standalone JSON code block, with all the results in it.

=== Example format ===
```json
["mentioned_correct", "not_mentioned", "mentioned_incorrect"]
```"""