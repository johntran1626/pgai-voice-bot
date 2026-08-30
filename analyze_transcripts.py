"""
analyze_transcripts.py

Reads every transcript in transcripts/ and asks Claude to draft a first
pass of possible bugs/issues, one scenario at a time. This is a STARTING
POINT, not your final bug report -- read every transcript yourself,
confirm each finding is real (LLMs hallucinate issues that aren't there),
edit the wording, and delete anything that's just a nitpick.

Run it with:
    python analyze_transcripts.py

Output goes to bug_report_draft.md -- review it, then write your real
findings into bug_report.md by hand.
"""

import os
import glob
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

ANALYSIS_PROMPT = """You are helping QA a medical practice's AI phone agent.
Below is a transcript of a call between a simulated patient and that AI agent.

Read it carefully and list any real issues you find, such as:
- Factually wrong or made-up information (e.g. confirming something that
  shouldn't be possible, like booking outside office hours)
- Failing to ask for information it needs (identity verification, etc.)
- Misunderstanding or ignoring what the patient said
- Awkward, robotic, or unnatural responses
- Giving medical advice it shouldn't
- Failing to handle an interruption or a vague request gracefully

For each issue, give: a one-sentence description, a severity (High/Medium/Low),
and roughly where in the transcript it happens (quote a short snippet).

If you find nothing notable, say so plainly -- do not invent issues just to
have something to report.

TRANSCRIPT:
---
{transcript}
---
"""


def main():
    transcript_files = sorted(glob.glob("transcripts/*.txt"))
    if not transcript_files:
        print("No transcripts found in transcripts/. Run run_calls.py first.")
        return

    output_lines = ["# Bug Report - Draft (AI-generated first pass, review before using)\n"]

    for path in transcript_files:
        scenario_id = os.path.basename(path).replace(".txt", "")
        print(f"Analyzing {scenario_id}...")
        with open(path) as f:
            transcript = f.read()

        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=1000,
            messages=[
                {
                    "role": "user",
                    "content": ANALYSIS_PROMPT.format(transcript=transcript),
                }
            ],
        )
        analysis = response.content[0].text

        output_lines.append(f"## {scenario_id}\n")
        output_lines.append(analysis)
        output_lines.append("\n---\n")

    with open("bug_report_draft.md", "w") as f:
        f.write("\n".join(output_lines))

    print("\nDraft written to bug_report_draft.md")
    print("Now: read every transcript yourself, verify each finding, and")
    print("write your real bug_report.md by hand based on what's actually true.")


if __name__ == "__main__":
    main()
