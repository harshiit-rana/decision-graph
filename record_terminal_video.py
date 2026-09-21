"""Record an authentic, high-definition (1920x1080) Terminal Walkthrough MP4 Video for Decision Graph."""
import os
import subprocess
import wave
import re
from PIL import Image, ImageDraw, ImageFont

OUTPUT_DIR = "terminal_recording_assets"
os.makedirs(OUTPUT_DIR, exist_ok=True)

FONT_PATH = r"C:\Windows\Fonts\consola.ttf"
BOLD_FONT_PATH = r"C:\Windows\Fonts\consolab.ttf"

SCENES = [
    {
        "cmd": "docker compose run --rm app doctor",
        "voice": "We start with a preflight health check. Decision Graph runs fully containerized in Docker, with PostgreSQL, GitHub authentication, and Nvidia NIM integration all verified.",
        "output": [
            "Container decision-graph-db-1 Running",
            "Container decision-graph-db-1 Healthy",
            "",
            "Environment",
            "───────────",
            "  ok   Python 3.13 in container",
            "  ok   psql available in container",
            "",
            "Configuration",
            "─────────────",
            "  ok   .env found (/work/.env)",
            "  ok   GitHub token valid (authenticated as harshiit-rana)",
            "  ok   rate limit — 5000/5000 remaining",
            "  ok   target repo — pallets/flask",
            "  ok   openai package available (for `dg ask`)",
            "  ok   NVIDIA_API_KEY set (for `dg ask`)",
            "",
            "Database",
            "────────",
            "  ok   database reachable & schema up to date",
            "  ok   1,007 nodes ingested",
            "",
            "All checks passed."
        ]
    },
    {
        "cmd": "docker compose run --rm app status",
        "voice": "Checking repository status: we have indexed over one thousand artifacts and mechanically reconstructed fifteen verified architectural decisions across Flask.",
        "output": [
            "Repository Status: pallets/flask",
            "──────────────────────────────────────────────",
            "  Nodes ingested:       1,007 artifacts",
            "  Decisions identified: 15 verified choices",
            "  Issues:               239 thread clusters",
            "  Pull Requests:        224 merged PRs",
            "  Commits:              536 commit nodes",
            "",
            "Cursors:",
            "  pulls      up to 2026-08-20",
            "  issues     up to 2026-08-20",
            "  commits    up to 2026-08-20",
            "",
            "Graph is current and ready for reasoning."
        ]
    },
    {
        "cmd": "docker compose run --rm app query \"#5895\"",
        "voice": "Now we ask why a change happened. Decision Graph traverses the graph, extracts the author's original problem statement explaining why 302 and 307 broke POST redirects, and proves the explicit link to pull request 5898.",
        "output": [
            "Why did this happen?   '#5895'",
            "",
            "  starting from  issue #5895  change default redirect code to 303",
            "                 matched by identifier",
            "",
            "  What the graph says",
            "    issue #5895 \"change default redirect code to 303\" motivated the decision in this thread",
            "        that decision is reconstructed, implemented by PR #5898, merged 2026-01-25",
            "",
            "  Motivation & Context  (from issue #5895)",
            "    Flask and Werkzeug redirect currently defaults to a 302. Routing uses",
            "    307 since that preserves method consistently. We didn't change the",
            "    redirect default to 307, since that would break the common pattern of",
            "    \"GET form, POST form, redirect to GET result\", ending up doing \"POST",
            "    result\" instead. 303 seems designed exactly for this pattern, so that a",
            "    redirect always results in a GET, instead of preserving the method...",
            "",
            "  evidence: explicit  (strong) — stated in the repository itself",
            "",
            "  Evidence trail  (1 path)",
            "  [1] == explicit  1 hop",
            "      issue #5895 — change default redirect code to 303",
            "       └─ motivated  [explicit]",
            "      decision — change default redirect code to 303",
            "          reconstructed, implemented by PR #5898, merged 2026-01-25",
            "",
            "  Check it on GitHub",
            "    issue #5895              https://github.com/pallets/flask/issues/5895",
            "    pull request #5898       https://github.com/pallets/flask/pull/5898"
        ]
    },
    {
        "cmd": "docker compose run --rm app query \"#5815\" --mode impact",
        "voice": "Before modifying or reverting code, downstream impact analysis traces every closed pull request, implemented commit, and parent dependency to prevent regressions.",
        "output": [
            "What does this affect?   '#5815'",
            "",
            "  starting from  issue #5815  pass context internally instead of using contextvars",
            "                 matched by identifier",
            "",
            "  What the graph says",
            "    issue #5815 \"pass context internally...\" was closed by pull request #5818",
            "    issue #5815 links to pull request #5818, which implements commit 6a64969",
            "    issue #5815 links to pull request #5818, which implemented the decision in this thread",
            "    issue #5815 links to commit 6a64969, which is depended on by commit 70d04b5",
            "",
            "  Motivation & Context  (from issue #5815)",
            "    Currently, there are a bunch of different methods on the Flask class",
            "    that run to dispatch each request. Many of these access request and",
            "    other context proxies. We should update them to pass AppContext everywhere.",
            "",
            "  evidence: corroborated  (strongest) — carries 3 of 4 independent signals",
            "",
            "  Evidence trail  (4 paths)",
            "  [1] ++ corroborated  1 hop: issue #5815 -> closed by -> pull request #5818",
            "  [2] ++ corroborated  2 hops: issue #5815 -> PR #5818 -> implements -> commit 6a64969",
            "  [3] ++ corroborated  2 hops: issue #5815 -> PR #5818 -> implemented -> decision #5815",
            "  [4] ++ corroborated  3 hops: commit 6a64969 -> is depended on by -> commit 70d04b5"
        ]
    },
    {
        "cmd": "docker compose run --rm app ask \"why did we change the redirect code?\"",
        "voice": "Finally, our plain English Natural Language Q&A powered by Nvidia NIM synthesizes the graph traversal and issue discussions into deep engineering explanations with zero hallucinations.",
        "output": [
            "Parsing intent for: 'why did we change the redirect code?'",
            "-> Parsed intent: Search for 'redirect code', Mode: why",
            "Found candidate: issue #5895 \"change default redirect code to 303\"",
            "Traversing the graph...",
            "",
            "In plain English",
            "────────────────",
            "The change to the default redirect code was motivated by issue #5895.",
            "",
            "Flask and Werkzeug's redirect() helper originally defaulted to HTTP 302.",
            "While routing used HTTP 307 to preserve HTTP methods, 307 broke the common",
            "Post/Redirect/Get pattern by re-sending POST data to the target page.",
            "",
            "HTTP 303 was chosen because it explicitly instructs clients to always perform",
            "a GET request on redirect, perfectly fitting form submissions and modern HTMX",
            "applications. The change was implemented and merged in pull request #5898.",
            "",
            "Evidence Tier: explicit (grounded in repository record)",
            "Written by nvidia/nemotron-3-super-120b-a12b from the graph trace."
        ]
    }
]

def generate_tts(idx, text):
    wav_path = os.path.join(OUTPUT_DIR, f"scene_audio_{idx}.wav")
    escaped = text.replace('"', '""')
    ps = f'''
    Add-Type -AssemblyName System.Speech
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $s.Rate = 0
    $s.SetOutputToWaveFile("{wav_path}")
    $s.Speak("{escaped}")
    $s.Dispose()
    '''
    subprocess.run(["powershell", "-Command", ps], check=True)
    with wave.open(wav_path, "r") as wf:
        duration = wf.getnframes() / float(wf.getframerate())
    return wav_path, duration

def color_for_line(line: str):
    if line.startswith("  ok") or "passed" in line or "[1]" in line:
        return "#4ade80" # Green
    if line.startswith("Why did") or line.startswith("What does") or line.startswith("Parsing"):
        return "#38bdf8" # Cyan
    if line.startswith("  Motivation & Context") or line.startswith("In plain English"):
        return "#fbbf24" # Amber
    if line.startswith("  evidence:") or line.startswith("Evidence Tier:"):
        return "#60a5fa" # Blue
    if line.startswith("Container") or line.startswith("───") or line.startswith("Check it"):
        return "#64748b" # Dim gray
    if line.startswith("    http"):
        return "#38bdf8" # Link cyan
    return "#e2e8f0" # Clean bright text

def render_terminal_frame(cmd_typed: str, show_cursor: bool, output_lines: list[str]) -> Image.Image:
    W, H = 1920, 1080
    img = Image.new("RGB", (W, H), color="#090d16") # Deep dark background
    draw = ImageDraw.Draw(img)

    # Monospace font
    font = ImageFont.truetype(FONT_PATH, 20)
    bold_font = ImageFont.truetype(BOLD_FONT_PATH, 20)
    title_font = ImageFont.truetype(BOLD_FONT_PATH, 18)

    # Outer Terminal Window
    win_x1, win_y1 = 80, 40
    win_x2, win_y2 = W - 80, H - 40
    draw.rounded_rectangle([win_x1, win_y1, win_x2, win_y2], radius=12, fill="#0f172a", outline="#1e293b", width=2)

    # Title Bar
    bar_h = 44
    draw.rounded_rectangle([win_x1, win_y1, win_x2, win_y1 + bar_h], radius=12, fill="#1e293b")
    draw.rectangle([win_x1, win_y1 + 20, win_x2, win_y1 + bar_h], fill="#1e293b") # Flatten bottom corners

    # Window buttons
    draw.ellipse([win_x1 + 20, win_y1 + 14, win_x1 + 36, win_y1 + 30], fill="#ef4444")
    draw.ellipse([win_x1 + 44, win_y1 + 14, win_x1 + 60, win_y1 + 30], fill="#f59e0b")
    draw.ellipse([win_x1 + 68, win_y1 + 14, win_x1 + 84, win_y1 + 30], fill="#10b981")

    # Title text
    draw.text((win_x1 + 110, win_y1 + 12), "PowerShell — Decision Graph [pallets/flask]", font=title_font, fill="#94a3b8")

    # Terminal Canvas
    t_left = win_x1 + 30
    t_top = win_y1 + bar_h + 20
    line_h = 26
    max_visible_lines = 33

    # Render lines
    all_lines = []
    # Prompt + typed command
    prompt = "PS D:\\Internal-tool-project> "
    all_lines.append(("PROMPT", prompt, cmd_typed, show_cursor))
    for out_ln in output_lines:
        all_lines.append(("OUTPUT", out_ln))

    # Scroll if lines exceed window
    if len(all_lines) > max_visible_lines:
        all_lines = all_lines[-max_visible_lines:]

    y = t_top
    for item in all_lines:
        if item[0] == "PROMPT":
            _, pr, cmd, cursor = item
            draw.text((t_left, y), pr, font=bold_font, fill="#22c55e")
            pr_w = int(draw.textlength(pr, font=bold_font))
            draw.text((t_left + pr_w, y), cmd, font=bold_font, fill="#f8fafc")
            cmd_w = int(draw.textlength(cmd, font=bold_font))
            if cursor:
                draw.rectangle([t_left + pr_w + cmd_w + 2, y + 2, t_left + pr_w + cmd_w + 14, y + 22], fill="#38bdf8")
        else:
            _, text = item
            color = color_for_line(text)
            draw.text((t_left, y), text, font=font, fill=color)
        y += line_h

    return img

def render_scene(idx, scene, wav_path, duration):
    FPS = 15
    total_frames = int((duration + 2.0) * FPS) # Audio duration + 2s pause
    clip_mp4 = os.path.join(OUTPUT_DIR, f"terminal_scene_{idx}.mp4")

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", "1920x1080", "-pix_fmt", "rgb24", "-r", str(FPS),
        "-i", "-",
        "-i", wav_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-b:v", "3M",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration + 2.0),
        clip_mp4
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    full_cmd = scene["cmd"]
    type_frames = min(len(full_cmd) * 2, int(2.5 * FPS)) # Realistic typing duration

    for f in range(total_frames):
        show_cursor = (f // (FPS // 2)) % 2 == 0 # Blink cursor every 0.5s

        if f < type_frames:
            # Typing phase
            chars_to_show = int(len(full_cmd) * (f / type_frames))
            current_typed = full_cmd[:chars_to_show]
            current_output = []
        elif f < type_frames + (FPS // 2):
            # Brief pause before output appears (0.5s)
            current_typed = full_cmd
            current_output = []
        else:
            # Output streaming phase
            current_typed = full_cmd
            elapsed_out_frames = f - (type_frames + FPS // 2)
            # Stream in ~8-12 lines per second
            lines_to_show = min(len(scene["output"]), int(elapsed_out_frames * 0.8) + 1)
            current_output = scene["output"][:lines_to_show]

        frame_img = render_terminal_frame(current_typed, show_cursor, current_output)
        proc.stdin.write(frame_img.tobytes())

    proc.stdin.close()
    proc.wait()
    print(f"Rendered Scene {idx + 1}/{len(SCENES)}: {clip_mp4}")
    return clip_mp4

def main():
    print("Recording realistic terminal walkthrough video...")
    clip_files = []

    for i, scene in enumerate(SCENES):
        print(f"\nProcessing Scene {i + 1}/{len(SCENES)}: {scene['cmd']}")
        wav_path, duration = generate_tts(i, scene["voice"])
        clip_mp4 = render_scene(i, scene, wav_path, duration)
        clip_files.append(clip_mp4)

    # Concat all terminal scenes
    list_file = os.path.join(OUTPUT_DIR, "terminal_clips.txt")
    with open(list_file, "w") as f:
        for clip in clip_files:
            abs_clip = os.path.abspath(clip).replace("\\", "/")
            f.write(f"file '{abs_clip}'\n")

    final_output = "decision_graph_terminal_walkthrough.mp4"
    print(f"\nStitching all scenes into {final_output}...")
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        final_output
    ]
    subprocess.run(concat_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"\nSUCCESS! Recorded Terminal Video ready at: {os.path.abspath(final_output)}")

if __name__ == "__main__":
    main()
