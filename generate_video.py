"""Generate a high-quality MP4 Hackathon Demo Video for Decision Graph using Pillow, PowerShell TTS, and ffmpeg."""
import os
import subprocess
import wave
from PIL import Image, ImageDraw, ImageFont

OUTPUT_DIR = "demo_video_assets"
os.makedirs(OUTPUT_DIR, exist_ok=True)

SLIDES = [
    {
        "title": "DECISION GRAPH",
        "subtitle": "Organizational Intelligence Engine for Engineering Teams",
        "tagline": "Git tracks how code evolves. Decision Graph tracks why engineering decisions were made.",
        "points": [
            "Reconstructs causal rationale behind legacy code & technical decisions",
            "Maps connections between GitHub issues, PRs, commits, and reviews",
            "Pure graph reasoning with verifiable evidence tiers (explicit, corroborated, inferred)",
            "Containerized 3-engine architecture with Nvidia NIM LLM synthesis"
        ],
        "speech": "Welcome to Decision Graph, an organizational intelligence engine that reconstructs the causal chain of engineering decisions from repository history."
    },
    {
        "title": "1. Ingested Knowledge Graph (pallets/flask)",
        "subtitle": "Full Temporal Graph over Repository Artifacts",
        "tagline": "Rebuilding institutional memory that is normally scattered and lost.",
        "points": [
            "Target Repository: pallets/flask",
            "1,007 artifacts indexed: issues, pull requests, commits, and releases",
            "15 architectural decisions mechanically reconstructed from multi-signal clusters",
            "Sub-second traversal over PostgreSQL with pg_trgm and recursive graph queries"
        ],
        "speech": "Decision Graph connects issues, pull requests, and commit histories into a time-aware knowledge graph, reconstructing fifteen major architectural decisions across Flask."
    },
    {
        "title": "2. Causal Reconstruction: Why did this happen?",
        "subtitle": "dg query '#5895' (change default redirect code to 303)",
        "tagline": "Directly extracts the author's problem statement and evidence trail.",
        "points": [
            "Motivation & Context (from issue #5895):",
            "  'Flask redirect defaulted to 302. Routing used 307 which preserved method,",
            "   breaking Post-Redirect-Get form workflows. 303 guarantees a GET redirect.'",
            "Evidence Tier: explicit (strong) — stated directly in repo history",
            "Evidence Trail: issue #5895 -> motivated -> decision (implemented by PR #5898)"
        ],
        "speech": "When an engineer asks why a change occurred, Decision Graph reconstructs the causal chain, extracting the author's original problem statement, grading the evidence tier, and tracing the exact merged PR."
    },
    {
        "title": "3. Downstream Impact Analysis: What does this affect?",
        "subtitle": "dg query '#5815' --mode impact",
        "tagline": "Predicting the consequences of modifying or reverting code.",
        "points": [
            "Starting from: issue #5815 ('pass context internally instead of contextvars')",
            "[1] issue #5815 was closed by pull request #5818 (corroborated)",
            "[2] pull request #5818 implements commit 6a64969 (corroborated)",
            "[3] commit 6a64969 is depended on by merge commit 70d04b5 (explicit)",
            "Prevents regressions by revealing hidden downstream dependencies before editing."
        ],
        "speech": "Need to know what depends on a change before modifying it? Downstream impact analysis traces every closed pull request, implemented commit, and parent dependency."
    },
    {
        "title": "4. High-Contrast Visual Graph Diagrams",
        "subtitle": "dg query '#5895' --format mermaid",
        "tagline": "Ready to paste into GitHub PR descriptions and design documents.",
        "points": [
            "Mermaid & Graphviz DOT export with high-contrast color scheme:",
            "  * Amber Hexagons: Reconstructed Decisions",
            "  * Light Blue Stadiums: Issues & Motivations",
            "  * Light Green Rectangles: Pull Requests & Implementers",
            "  * Indigo Circles: Commits & Changes",
            "Readable in both light and dark modes across GitHub and mermaid.live."
        ],
        "speech": "Answers export directly as color-coded Mermaid diagrams and HTML reports ready to paste into GitHub issues, pull requests, or architecture design reviews."
    },
    {
        "title": "5. Natural Language Q&A (Nvidia NIM)",
        "subtitle": "dg ask 'why did we change the redirect code?'",
        "tagline": "Deep LLM synthesis grounded in actual graph traversal and issue text.",
        "points": [
            "Powered by Nvidia NIM (nemotron-3-super-120b)",
            "Synthesizes both the graph traversal and the author's issue description:",
            "  * Explains the HTTP 302 vs 307 vs 303 specifications",
            "  * Highlights HTMX and Post/Redirect/Get form pattern requirements",
            "  * Zero hallucinations: grounded in explicit repository evidence"
        ],
        "speech": "For plain English questions, Nvidia NIM Nemotron synthesizes the graph traversal and issue discussions into deep engineering explanations with zero hallucinations."
    },
    {
        "title": "DECISION GRAPH",
        "subtitle": "Turning Commit History into Institutional Intelligence",
        "tagline": "Stop guessing why legacy code was written. Understand your codebase.",
        "points": [
            "Dockerized 3-tier architecture: PostgreSQL 16 + Python 3.13 + Nvidia NIM",
            "223 unit tests protecting graph reasoning and evidence invariants",
            "Production-ready CLI, interactive menu launcher, and HTML executive reports",
            "Built for the Hackathon. Thank you!"
        ],
        "speech": "Decision Graph turns raw commit histories into actionable institutional memory. Stop guessing legacy code. Understand your codebase. Thank you!"
    }
]

FONT_PATH = r"C:\Windows\Fonts\consola.ttf"
BOLD_FONT_PATH = r"C:\Windows\Fonts\consolab.ttf"

def render_slide(index, slide):
    width, height = 1920, 1080
    img = Image.new("RGB", (width, height), color="#0f172a") # Dark slate navy
    draw = ImageDraw.Draw(img)

    # Fonts
    title_font = ImageFont.truetype(BOLD_FONT_PATH, 56)
    sub_font = ImageFont.truetype(FONT_PATH, 32)
    tag_font = ImageFont.truetype(FONT_PATH, 26)
    body_font = ImageFont.truetype(FONT_PATH, 28)
    badge_font = ImageFont.truetype(BOLD_FONT_PATH, 20)

    # Top accent bar
    draw.rectangle([0, 0, width, 12], fill="#38bdf8")

    # Header badge
    draw.rounded_rectangle([100, 50, 340, 85], radius=6, fill="#1e293b", outline="#38bdf8", width=1)
    draw.text((115, 57), "DECISION GRAPH", font=badge_font, fill="#38bdf8")

    # Slide Title
    draw.text((100, 110), slide["title"], font=title_font, fill="#f8fafc")
    draw.text((100, 185), slide["subtitle"], font=sub_font, fill="#94a3b8")

    # Tagline card
    draw.rounded_rectangle([100, 245, width - 100, 310], radius=8, fill="#1e293b", outline="#334155", width=1)
    draw.text((120, 262), slide["tagline"], font=tag_font, fill="#38bdf8")

    # Main Card
    card_top = 340
    card_bottom = height - 90
    draw.rounded_rectangle([100, card_top, width - 100, card_bottom], radius=12, fill="#020617", outline="#1e293b", width=2)

    # Window Controls (mac/terminal style dots)
    draw.ellipse([130, card_top + 25, 144, card_top + 39], fill="#ef4444")
    draw.ellipse([154, card_top + 25, 168, card_top + 39], fill="#f59e0b")
    draw.ellipse([178, card_top + 25, 192, card_top + 39], fill="#10b981")
    draw.text((215, card_top + 22), "terminal -- decision-graph v1.0", font=ImageFont.truetype(FONT_PATH, 18), fill="#64748b")
    draw.line([100, card_top + 55, width - 100, card_top + 55], fill="#1e293b", width=1)

    # Bullet points / Terminal output
    y = card_top + 80
    for pt in slide["points"]:
        color = "#e2e8f0"
        if pt.startswith("  * Amber") or pt.startswith("  * Light"):
            color = "#fde68a"
        elif "explicit" in pt:
            color = "#4ade80"
        elif "corroborated" in pt:
            color = "#38bdf8"
        elif pt.startswith("Motivation & Context"):
            color = "#fbbf24"
        elif pt.startswith("Evidence Tier") or pt.startswith("Evidence Trail"):
            color = "#60a5fa"
        
        draw.text((140, y), pt, font=body_font, fill=color)
        y += 48

    # Footer
    draw.text((100, height - 60), "Decision Graph Engine  *  GitHub Intelligence  *  Hackathon Demo", font=ImageFont.truetype(FONT_PATH, 20), fill="#475569")
    draw.text((width - 240, height - 60), f"Slide {index + 1} of {len(SLIDES)}", font=ImageFont.truetype(FONT_PATH, 20), fill="#475569")

    slide_path = os.path.join(OUTPUT_DIR, f"slide_{index}.png")
    img.save(slide_path)
    return slide_path

def generate_tts_wav(index, text):
    wav_path = os.path.join(OUTPUT_DIR, f"audio_{index}.wav")
    escaped = text.replace('"', '""')
    ps_cmd = f'''
    Add-Type -AssemblyName System.Speech
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $s.Rate = 0
    $s.SetOutputToWaveFile("{wav_path}")
    $s.Speak("{escaped}")
    $s.Dispose()
    '''
    subprocess.run(["powershell", "-Command", ps_cmd], check=True)
    return wav_path

def get_audio_duration(wav_path):
    with wave.open(wav_path, "r") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / float(rate)

def main():
    print("Generating demo video slides and voiceovers...")
    clip_files = []
    
    for i, slide in enumerate(SLIDES):
        img_path = render_slide(i, slide)
        wav_path = generate_tts_wav(i, slide["speech"])
        duration = get_audio_duration(wav_path) + 1.2 # add pause at end of each slide
        clip_mp4 = os.path.join(OUTPUT_DIR, f"clip_{i}.mp4")
        
        print(f"Creating Clip {i + 1}/{len(SLIDES)} ({duration:.1f}s)...")
        # Build individual clip: loop slide image for audio duration + audio
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", img_path,
            "-i", wav_path,
            "-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-t", str(duration),
            clip_mp4
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        clip_files.append(clip_mp4)

    # Concat all clips into final video
    list_file = os.path.join(OUTPUT_DIR, "clips.txt")
    with open(list_file, "w") as f:
        for clip in clip_files:
            abs_clip = os.path.abspath(clip).replace("\\", "/")
            f.write(f"file '{abs_clip}'\n")

    final_output = "decision_graph_hackathon_demo.mp4"
    print(f"Concatenating all clips into {final_output}...")
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        final_output
    ]
    subprocess.run(concat_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"\nSUCCESS! Hackathon Demo Video generated: {os.path.abspath(final_output)}")

if __name__ == "__main__":
    main()
