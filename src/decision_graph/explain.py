"""NLP Layer for the Organizational Intelligence Engine.

Provides an LLM-based interface to translate natural language questions into
graph queries, and summarize the resulting paths into human-readable explanations.
"""
from __future__ import annotations

import json
import os
import sys

from .reasoning import Answer

# We import OpenAI inside functions so that if the API key is not configured,
# or the dependency isn't installed for some reason, the rest of the CLI still boots.

MODEL = "meta/llama-3.1-70b-instruct"

def get_client():
    try:
        from openai import OpenAI
    except ImportError:
        print("error: the 'openai' python package is missing. Rebuild the docker image.", file=sys.stderr)
        sys.exit(2)

    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        print("error: NVIDIA_API_KEY is not set in .env", file=sys.stderr)
        sys.exit(2)
        
    return OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=api_key
    )

def parse_intent(question: str) -> tuple[str, str]:
    """Parse a natural language question into a search query and mode ('why' or 'impact')."""
    client = get_client()
    
    prompt = f"""You are a query parser for a GitHub repository decision graph.
The user will ask a natural language question about the codebase.
Your job is to extract:
1. The exact search entity: A title, pull request number (e.g. #123), or commit sha.
2. The mode: 'why' (if asking for motivation/reason) or 'impact' (if asking what it affects/breaks downstream).

Output valid JSON ONLY in this format:
{{"query": "extract search term", "mode": "why" or "impact"}}

User question: {question}
"""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        response_format={"type": "json_object"}
    )
    
    result = json.loads(response.choices[0].message.content)
    return result.get("query", ""), result.get("mode", "why")

def summarize_answer(question: str, answer: Answer) -> str:
    """Take the raw paths and summarize them for the user."""
    client = get_client()
    
    # We serialize the Answer into a clean textual format for the LLM
    text_paths = []
    for i, path in enumerate(answer.paths, 1):
        lines = []
        lines.append(f"Path {i} (tier={path.tier}, depth={path.depth}):")
        
        # start node
        start_id = path.node_ids[0]
        start_type = path.types.get(start_id, "?")
        start_title = path.titles.get(start_id, "")
        lines.append(f"  Start: {start_type}:{start_id} \"{start_title}\"")
        
        for step, node_id in zip(path.steps, path.node_ids[1:]):
            direction = "->" if step.to_node_id == node_id else "<-"
            lines.append(f"    {direction} {step.edge_type} ({step.extractor})")
            
            node_type = path.types.get(node_id, "?")
            node_title = path.titles.get(node_id, "")
            lines.append(f"    Node: {node_type}:{node_id} \"{node_title}\"")
        
        text_paths.append("\n".join(lines))
        
    paths_str = "\n\n".join(text_paths)
    
    prompt = f"""You are an Organizational Intelligence Engine explaining a decision graph to an engineer.
The user asked: "{question}"

Here are the raw graph traversal paths found in the database:
{paths_str if paths_str else 'NO PATHS FOUND'}

Task: 
Explain the result of this query clearly and concisely in plain English. 
- Do not just read the paths back mechanically. Synthesize them.
- If multiple paths lead to the same decision, group them.
- State clearly what motivated the change, or what the change impacts.
- If NO PATHS FOUND, politely state that the graph does not contain the answer.
"""
    
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return response.choices[0].message.content
