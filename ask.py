import os
import sys

from google import genai

import file_search_store

SYSTEM_PROMPT = """You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply."""

# Free tier: 3.1-flash-lite allows 500 requests/day, 3.8-flash only 20.
MODEL = os.environ.get("MODEL", "gemini-3.1-flash-lite")
DEFAULT_QUESTION = "How do I add a YouTube video?"


def ask_optibot(question):
    client = genai.Client(api_key=os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY"))
    interaction = client.interactions.create(
        model=MODEL,
        system_instruction=SYSTEM_PROMPT,
        input=question,
        tools=[{"type": "file_search", "file_search_store_names": [file_search_store.get_or_create_store(client)]}],
        store=False,
    )
    answer_lines = []
    for step in interaction.steps:
        if step.type != "model_output":
            continue
        for block in step.content:
            if block.type != "text":
                continue
            answer_lines.append(block.text)
            cited_files = {a.file_name for a in block.annotations or [] if a.type == "file_citation"}
            answer_lines += [f"  [source: {name}]" for name in sorted(cited_files)]
    return "\n".join(answer_lines)


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or DEFAULT_QUESTION
    print(f"Q: {question}\n\n{ask_optibot(question)}")
