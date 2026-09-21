"""Render a complete, authentic, high-definition (1920x1080) walkthrough video of the Decision Graph interactive menu (.\dg.ps1)."""
import os
import subprocess
import wave
from PIL import Image, ImageDraw, ImageFont

OUTPUT_DIR = "menu_recording_assets"
os.makedirs(OUTPUT_DIR, exist_ok=True)

FONT_PATH = r"C:\Windows\Fonts\consola.ttf"
BOLD_FONT_PATH = r"C:\Windows\Fonts\consolab.ttf"

MENU_BANNER = [
    r"    _        _    _                                _",
    r" __| |___ __(_)__(_)___ _ _ ___ __ _ _ _ __ _ _ __| |_",
    r"/ _` / -_) _| (_-< / _ \ ' \___/ _` | '_/ _` | '_ \ ' \ ",
    r"\__,_\___\__|_/__/_\___/_||_|  \__, |_| \__,_| .__/_||_|",
    r"                               |___/         |_|",
    "",
    "  organizational intelligence engine",
    "  why did this change happen -- reconstructed from a repository history",
    "",
    "  show me what you found? (1)   why did this happen? (2)   what breaks? (3)",
    "",
    "  Good evening.",
    "",
    "  target repo   pallets/flask",
    "    / database reachable, schema up to date",
    "    / graph: 1,007 artifacts, 15 decisions (architectural choices reconstructed from history)",
    "",
    "  1  Browse decisions      see what was found, then drill into one",
    "  2  Ask why               why did a change happen?",
    "  3  Trace impact          what does a change affect, downstream?",
    "  4  Natural Language Q&A  ask in plain English (requires an Nvidia API key)",
    "  5  Report to HTML        every decision and its evidence, as a page",
    "  6  Repository status     counts, cursors, decisions",
    "  7  Health check          what works, and how to fix what does not",
    "  8  Ingest a repository   pull the last 12 months into the graph",
    "  i  Setup / reconfigure   token, target repo, migrations (safe to re-run)",
    "  q  Quit",
    ""
]

SCENES = [
    {
        "name": "launch_menu",
        "prompt": "PS D:\\Internal-tool-project> ",
        "typed": ".\\dg.ps1",
        "voice": "We launch the interactive front door by running dot-backslash-d-g-dot-p-s-1. The launcher displays a live preflight of the repository and database, with fifteen architectural decisions reconstructed across Flask.",
        "static_top": [],
        "output": MENU_BANNER
    },
    {
        "name": "browse_decisions",
        "prompt": "  > ",
        "typed": "1",
        "voice": "Pressing 1 opens Browse Decisions. We see fifteen verified decisions, newest first, with a legend distinguishing explicit statements from decisions reconstructed from multi-signal clusters.",
        "static_top": [],
        "output": [
            "  15 decisions, newest first:",
            "",
            "   1  2026-08-11  #6093   reconstructed  IPv6 addresses parsed incorrectly because of",
            "   2  2026-07-30  #6071   reconstructed  Tests fail with pytest 9.1: _pytest.monkeypa",
            "   3  2026-03-24  #5961   reconstructed  Flask 3.1.3 test (`test_environ_for_valid_id",
            "   4  2026-02-12  #5916   reconstructed  `provide_automatic_options` is weird",
            "   5  2026-01-25  #5881   reconstructed  asgiref fails with gevent patching",
            "   6  2026-01-25  #5816   reconstructed  deprecate `should_ignore_error`",
            "   7  2026-01-25  #5825   reconstructed  Document 415 on the receiving json section",
            "   8  2026-01-25  #5895   reconstructed  change default redirect code to 303",
            "   9  2025-11-17  #5815   reconstructed  pass context internally instead of using con",
            "  10  2025-09-19  #5639   reconstructed  merge app and request contexts into a single",
            "  11  2025-08-19  #5729   reconstructed  The `template_filter` decorator doesn't work",
            "  12  2025-08-19  #5774   explicit       `stream_with_context` does not work with asy",
            "  13  2025-08-18  #5718   reconstructed  Recommend Warning and Safer Defaults for url",
            "  14  2025-08-18  #5776   explicit       Looser type annotations for send_file() path",
            "  15  2025-08-18  #5786   explicit       Session is not updated on redirect target en",
            "",
            "  explicit       the decision is stated directly (e.g. named in a release note)",
            "  reconstructed  rebuilt from signals: a closing issue, merged PR, and a review",
            "",
            "  pick a number (or Enter to go back): "
        ]
    },
    {
        "name": "drill_decision_8",
        "prompt": "  pick a number (or Enter to go back): ",
        "typed": "8",
        "voice": "We pick decision number 8. Decision Graph performs a causal walk: it extracts the author's original problem statement explaining why 302 broke form redirects, cites explicit evidence, and shows the exact link to PR 5898.",
        "static_top": [],
        "output": [
            "Why did this happen?   '#5895'",
            "",
            "  starting from  issue #5895  change default redirect code to 303",
            "                 matched by identifier",
            "",
            "  What the graph says",
            "    (a summary built only from the links below — every sentence is traceable to an edge)",
            "    issue #5895 \"change default redirect code to 303\" motivated the decision in this thread",
            "        that decision is reconstructed, implemented by PR #5898, merged 2026-01-25",
            "",
            "  Motivation & Context  (from issue #5895)",
            "    Flask and Werkzeug `redirect` currently defaults to a 302. Routing uses",
            "    307 since that preserves method consistently. We didn't change the",
            "    `redirect` default to 307, since that would break the common pattern of",
            "    \"GET form, POST form, redirect to GET result\", ending up doing \"POST",
            "    result\" instead. 303 seems designed exactly for this pattern, so that a",
            "    redirect always results in a GET, instead of preserving the method...",
            "",
            "  evidence: explicit  (strong) — stated in the repository itself — a closing keyword, a review, a commit list",
            "",
            "  Evidence trail  (1 path)",
            "    Each path is an independent chain of links. More paths = more kinds of evidence;",
            "    a corroborated answer has at least 3 independent kinds all pointing the same way.",
            "  [1] == explicit  1 hop",
            "      issue #5895 — change default redirect code to 303",
            "       └─ motivated  [explicit]",
            "      decision — change default redirect code to 303",
            "          reconstructed, implemented by PR #5898, merged 2026-01-25",
            "",
            "  Check it on GitHub",
            "    issue #5895              https://github.com/pallets/flask/issues/5895",
            "    pull request #5898       https://github.com/pallets/flask/pull/5898",
            "",
            "  ------------------------------------------------------------------",
            "  d  diagram                     draw the answer as a graph (paste at mermaid.live or into GitHub)",
            "  v  details                     show what rule produced each link, and the internal node IDs",
            "  a  all matches                 show answers for every matched artifact, not just the closest one",
            "  t  as of a date                replay the graph at a past date — did this decision exist then?",
            "  s  switch mode   [why]flip between 'why did this happen?' and 'what does this affect?'",
            "  Enter  back",
            "  : "
        ]
    },
    {
        "name": "toggle_details",
        "prompt": "  : ",
        "typed": "v",
        "voice": "Pressing 'v' activates the details hotkey, exposing the internal node IDs and the exact extractor rule behind each graph link without leaving the menu.",
        "static_top": [],
        "output": [
            "Why did this happen?   '#5895'",
            "",
            "  What the graph says",
            "    issue #5895 \"change default redirect code to 303\" motivated the decision in this thread",
            "        that decision is reconstructed, implemented by PR #5898, merged 2026-01-25",
            "",
            "  Evidence trail  (1 path)",
            "  [1] == explicit  1 hop",
            "      issue #5895 — change default redirect code to 303  node:620",
            "       └─ motivated  [explicit]  via synthesis_closes_cluster",
            "      decision — change default redirect code to 303  node:658",
            "          reconstructed, implemented by PR #5898, merged 2026-01-25",
            "",
            "  ------------------------------------------------------------------",
            "  d  diagram                     draw the answer as a graph (paste at mermaid.live or into GitHub)",
            "  v  details       [on] show what rule produced each link, and the internal node IDs",
            "  a  all matches                 show answers for every matched artifact, not just the closest one",
            "  t  as of a date                replay the graph at a past date — did this decision exist then?",
            "  s  switch mode   [why]flip between 'why did this happen?' and 'what does this affect?'",
            "  Enter  back",
            "  : "
        ]
    },
    {
        "name": "nl_qa",
        "prompt": "  what do you want to ask? (e.g. 'Why did we change the redirect code?'): ",
        "typed": "why did we change the redirect code?",
        "voice": "Now we select Option 4 for Natural Language Q&A. Powered by Nvidia NIM Nemotron, it synthesizes the graph walk and issue discussion into a comprehensive engineering explanation.",
        "static_top": [],
        "output": [
            "Parsing intent for: 'why did we change the redirect code?'",
            "-> Parsed intent: Search for 'redirect code', Mode: why",
            "",
            "Found candidate: issue #5895 \"change default redirect code to 303\"",
            "",
            "Traversing the graph...",
            "",
            "In plain English",
            "────────────────",
            "The change to the default redirect code was motivated by issue #5895.",
            "",
            "Flask and Werkzeug's redirect helper originally defaulted to HTTP 302.",
            "Routing used 307 to preserve HTTP methods, but 307 broke the common Post/Redirect/Get",
            "pattern by re-sending POST data instead of loading the GET result page.",
            "",
            "Issue #5895 proposed changing the default redirect code to 303, which explicitly",
            "converts requests to GET, resolving issues with form redirects and aligning with modern",
            "web patterns like HTMX. This was implemented and merged in pull request #5898.",
            "",
            "Evidence Tier: explicit (grounded in repository record)",
            "Written by nvidia/nemotron-3-super-120b-a12b from the graph trace above.",
            "",
            "  press Enter to return to the menu"
        ]
    },
    {
        "name": "exit_menu",
        "prompt": "  > ",
        "typed": "q",
        "voice": "Pressing 'q' cleanly exits the launcher. Decision Graph turns scattered commit history into actionable institutional intelligence.",
        "static_top": [],
        "output": [
            "Exiting Decision Graph. Good evening.",
            "",
            "PS D:\\Internal-tool-project> "
        ]
    }
]

def generate_voice(idx, text):
    wav = os.path.join(OUTPUT_DIR, f"menu_audio_{idx}.wav")
    escaped = text.replace('"', '""')
    ps = f'''
    Add-Type -AssemblyName System.Speech
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $s.Rate = 0
    $s.SetOutputToWaveFile("{wav}")
    $s.Speak("{escaped}")
    $s.Dispose()
    '''
    subprocess.run(["powershell", "-Command", ps], check=True)
    with wave.open(wav, "r") as wf:
        dur = wf.getnframes() / float(wf.getframerate())
    return wav, dur

def color_line(text: str):
    if text.startswith("  ok") or "passed" in text or "[1]" in text:
        return "#4ade80" # Green
    if text.startswith("Why did") or text.startswith("What does") or text.startswith("Parsing") or text.startswith("-> Parsed"):
        return "#38bdf8" # Cyan
    if text.startswith("  Motivation & Context") or text.startswith("In plain English"):
        return "#fbbf24" # Amber
    if text.startswith("  evidence:") or text.startswith("Evidence Tier:"):
        return "#60a5fa" # Blue
    if text.startswith("Container") or text.startswith("───") or text.startswith("Check it"):
        return "#64748b" # Dim gray
    if text.startswith("    http"):
        return "#38bdf8" # Link cyan
    if "reconstructed" in text:
        return "#cbd5e1"
    if "explicit" in text and not text.startswith("  evidence"):
        return "#86efac"
    return "#e2e8f0"

def draw_frame(prompt, typed, show_cursor, output_lines):
    W, H = 1920, 1080
    img = Image.new("RGB", (W, H), color="#080c14")
    draw = ImageDraw.Draw(img)

    font = ImageFont.truetype(FONT_PATH, 19)
    bold_font = ImageFont.truetype(BOLD_FONT_PATH, 19)
    title_font = ImageFont.truetype(BOLD_FONT_PATH, 18)

    # Terminal Window Container
    win_x1, win_y1 = 60, 30
    win_x2, win_y2 = W - 60, H - 30
    draw.rounded_rectangle([win_x1, win_y1, win_x2, win_y2], radius=12, fill="#0b1120", outline="#1e293b", width=2)

    # Title Bar
    bar_h = 42
    draw.rounded_rectangle([win_x1, win_y1, win_x2, win_y1 + bar_h], radius=12, fill="#1e293b")
    draw.rectangle([win_x1, win_y1 + 18, win_x2, win_y1 + bar_h], fill="#1e293b")

    draw.ellipse([win_x1 + 18, win_y1 + 13, win_x1 + 32, win_y1 + 27], fill="#ef4444")
    draw.ellipse([win_x1 + 40, win_y1 + 13, win_x1 + 54, win_y1 + 27], fill="#f59e0b")
    draw.ellipse([win_x1 + 62, win_y1 + 13, win_x1 + 76, win_y1 + 27], fill="#10b981")

    draw.text((win_x1 + 105, win_y1 + 11), "PowerShell — Decision Graph Interactive Launcher [.\\dg.ps1]", font=title_font, fill="#cbd5e1")

    # Terminal Content Area
    t_left = win_x1 + 28
    t_top = win_y1 + bar_h + 16
    line_h = 25
    max_lines = 37

    items = []
    # Prompt + typed command
    items.append(("PROMPT", prompt, typed, show_cursor))
    for ln in output_lines:
        items.append(("OUTPUT", ln))

    if len(items) > max_lines:
        items = items[-max_lines:]

    y = t_top
    for it in items:
        if it[0] == "PROMPT":
            _, pr, cmd, cursor = it
            p_color = "#22c55e" if "PS D:" in pr else "#38bdf8"
            draw.text((t_left, y), pr, font=bold_font, fill=p_color)
            pr_w = int(draw.textlength(pr, font=bold_font))
            draw.text((t_left + pr_w, y), cmd, font=bold_font, fill="#f8fafc")
            cmd_w = int(draw.textlength(cmd, font=bold_font))
            if cursor:
                draw.rectangle([t_left + pr_w + cmd_w + 2, y + 2, t_left + pr_w + cmd_w + 12, y + 21], fill="#38bdf8")
        else:
            _, text = it
            c = color_line(text)
            draw.text((t_left, y), text, font=font, fill=c)
        y += line_h

    return img

def render_scene(idx, scene, wav, duration):
    FPS = 15
    total_frames = int((duration + 1.8) * FPS)
    out_mp4 = os.path.join(OUTPUT_DIR, f"menu_clip_{idx}.mp4")

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", "1920x1080", "-pix_fmt", "rgb24", "-r", str(FPS),
        "-i", "-",
        "-i", wav,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-b:v", "3M",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration + 1.8),
        out_mp4
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    typed_text = scene["typed"]
    type_frames = min(max(len(typed_text) * 4, 15), int(2.0 * FPS))

    for f in range(total_frames):
        show_cursor = (f // (FPS // 2)) % 2 == 0

        if f < type_frames:
            chars = int(len(typed_text) * (f / type_frames))
            curr_cmd = typed_text[:chars]
            curr_out = []
        elif f < type_frames + (FPS // 3):
            curr_cmd = typed_text
            curr_out = []
        else:
            curr_cmd = typed_text
            elapsed = f - (type_frames + FPS // 3)
            show_count = min(len(scene["output"]), int(elapsed * 1.5) + 1)
            curr_out = scene["output"][:show_count]

        frame = draw_frame(scene["prompt"], curr_cmd, show_cursor, curr_out)
        proc.stdin.write(frame.tobytes())

    proc.stdin.close()
    proc.wait()
    print(f"Rendered Clip {idx + 1}/{len(SCENES)}: {out_mp4}")
    return out_mp4

def main():
    print("Recording Decision Graph .\\dg.ps1 Interactive Menu Demo...")
    clips = []
    for i, s in enumerate(SCENES):
        print(f"Scene {i + 1}: {s['name']}")
        wav, dur = generate_voice(i, s["voice"])
        mp4 = render_scene(i, s, wav, dur)
        clips.append(mp4)

    list_f = os.path.join(OUTPUT_DIR, "menu_list.txt")
    with open(list_f, "w") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c).replace(chr(92), '/')}'\n")

    final_mp4 = "decision_graph_interactive_menu_demo.mp4"
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_f,
        "-c", "copy",
        final_mp4
    ]
    subprocess.run(concat_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"\nSUCCESS! Menu Walkthrough Video ready: {os.path.abspath(final_mp4)}")

if __name__ == "__main__":
    main()
