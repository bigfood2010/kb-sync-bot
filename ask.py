import os
import sys

from file_search_store import open_gemini_documents

SYSTEM_PROMPT = """You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply."""

MODEL = os.environ.get("MODEL", "gemini-3.1-flash-lite")
DEFAULT_QUESTION = "How do I add a YouTube video?"


def ask_optibot(question):
    documents = open_gemini_documents()
    interaction = documents.client.interactions.create(
        model=MODEL,
        system_instruction=SYSTEM_PROMPT,
        input=question,
        tools=[{"type": "file_search", "file_search_store_names": [documents.store_name]}],
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
