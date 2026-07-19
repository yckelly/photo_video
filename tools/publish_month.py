#!/usr/bin/env python3
r"""
每月相簿發佈流程：上傳影片到 YouTube -> 產生影片頁 -> 更新 index.html -> commit。

用法（只要傳月資料夾名稱，不用完整路徑）：

  # 先看看會做什麼，不上傳、不改檔
  python3 tools/publish_month.py "20260519 - 20260616" \
      --album-url https://photos.app.goo.gl/xxxx \
      --thumb-url "https://lh3.googleusercontent.com/pw/AP1Gcz...=w100" \
      --dry-run

  # 正式跑
  python3 tools/publish_month.py "20260519 - 20260616" \
      --album-url https://photos.app.goo.gl/xxxx \
      --thumb-url "https://lh3.googleusercontent.com/pw/AP1Gcz...=w100" \
      --push

中斷後（YouTube 每日上限、網路斷線）重跑同一條指令即可，已上傳的會跳過。

事前準備：
  python3 -m pip install google-api-python-client google-auth-oauthlib
  client_secret.json / token.json 放在 --secrets-dir（預設 PHOTO_ROOT），不要進 repo。
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

PHOTO_ROOT = Path("/Users/ckb66885/Desktop/photo")
REPO_DIR = PHOTO_ROOT / "photo_video"
SITE_URL = "https://yckelly.github.io/photo_video/"

# 影片與順序檔在月資料夾底下的固定位置
VIDEO_SUBDIR = Path("傳") / "影片"
ORDER_FILENAME = "filenames.txt"
STATE_FILENAME = ".publish_state.json"

VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# 生日，用來自動算 subtitle 的年齡
KIDS = [
    ("Lucas", date(2018, 2, 14)),
    ("Matt", date(2019, 7, 19)),
    ("Nolan", date(2023, 1, 26)),
]

INDEX_ANCHOR = "<h2>育誠、Kelly的相簿集</h2>"
CN_DIGITS = "零一二三四五六七八九十"


# ---------------------------------------------------------------- 年齡計算

def cn_number(n: int) -> str:
    """0-19 的中文數字，年齡用不到更大的。"""
    if n <= 10:
        return CN_DIGITS[n]
    if n < 20:
        return "十" + CN_DIGITS[n - 10]
    raise ValueError(f"超出預期的數字: {n}")


def chinese_age(birth: date, ref: date) -> str:
    """ref 當天的年齡，四捨五入到最近的月（= ref 往後推 15 天再無條件捨去）。"""
    ref = ref + timedelta(days=15)
    years = ref.year - birth.year
    months = ref.month - birth.month
    if ref.day < birth.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12
    return f"{cn_number(years)}歲{cn_number(months)}個月"


def build_subtitle(end: date) -> str:
    return " ".join(
        f"{name} {chinese_age(birth, end)}" for name, birth in KIDS if birth <= end
    )


# ---------------------------------------------------------------- 資料夾解析

def parse_folder_name(folder_name: str):
    """'20260519 - 20260616' -> (顯示標題, html 檔名 slug, 結束日)。

    不是日期區間的資料夾（例如 '20250628 Taiwan'）回傳 (folder_name, None, None)，
    這種情況要自己給 --title 與 --subtitle。
    """
    m = re.fullmatch(r"(\d{8})\s*-\s*(\d{8})", folder_name.strip())
    if not m:
        return folder_name.strip(), None, None
    start_s, end_s = m.group(1), m.group(2)
    end = datetime.strptime(end_s, "%Y%m%d").date()
    return f"{start_s} - {end_s}", f"{start_s}_{end_s}", end


def resolve_month_dir(arg: str, photo_root: Path) -> Path:
    """接受資料夾名稱或完整路徑。"""
    p = Path(arg).expanduser()
    if p.is_absolute() or p.exists():
        month_dir = p
    else:
        month_dir = photo_root / arg
    if not month_dir.is_dir():
        sys.exit(f"找不到月資料夾: {month_dir}")
    return month_dir


# ---------------------------------------------------------------- 順序檔

def read_order_file(order_file: Path, video_dir: Path) -> list:
    """讀順序 txt，一行一個檔名，忽略空行與 # 註解。"""
    if not order_file.exists():
        sys.exit(f"找不到順序檔: {order_file}\n（先把要上傳的影片依序列進 {ORDER_FILENAME}）")

    names, seen = [], set()
    for line in order_file.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        if name in seen:
            sys.exit(f"順序檔裡有重複的檔名: {name}")
        seen.add(name)
        names.append(name)

    files, missing = [], []
    for name in names:
        p = video_dir / name
        (files if p.exists() else missing).append(p if p.exists() else name)

    if missing:
        sys.exit(
            "順序檔裡有找不到的檔案，先檢查檔名或路徑：\n"
            + "\n".join(f"  - {m}" for m in missing)
        )

    on_disk = {p.name for p in video_dir.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS}
    extra = on_disk - seen
    if extra:
        # 不是錯誤：有些片子是刻意挑掉不公開的
        print(f"提醒：資料夾裡有 {len(extra)} 支影片沒列進順序檔，這次不會上傳：")
        for e in sorted(extra):
            print(f"  - {e}")
        print()

    return files


# ---------------------------------------------------------------- 上傳狀態

def load_state(state_file: Path) -> dict:
    if state_file.exists():
        return json.loads(state_file.read_text(encoding="utf-8"))
    return {}


def save_state(state_file: Path, state: dict) -> None:
    state_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------- YouTube

def get_authenticated_service(secrets_dir: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_file = secrets_dir / "token.json"
    client_secret_file = secrets_dir / "client_secret.json"

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not client_secret_file.exists():
                sys.exit(f"找不到 {client_secret_file}，用 --secrets-dir 指到正確位置")
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def upload_video(youtube, filepath: Path, title: str) -> str:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from httplib2.error import HttpLib2Error

    body = {
        "snippet": {
            "title": title[:100],          # YouTube 標題上限 100 字
            "description": title,
            "categoryId": "22",            # People & Blogs
        },
        "status": {
            "privacyStatus": "unlisted",
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(filepath), chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    network_retries = 0
    while response is None:
        try:
            _, response = request.next_chunk()
            network_retries = 0
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504):
                print(f"    暫時性錯誤，5 秒後重試: {e}")
                time.sleep(5)
                continue
            if "uploadLimitExceeded" in str(e):
                sys.exit(
                    "\nYouTube 判定今天上傳太多支了（新頻道常見的每日上限，非 API 額度，"
                    "通常 24 小時內重置）。\n"
                    "已上傳的都記在月資料夾的 .publish_state.json，明天用同一條指令重跑會接著傳。"
                )
            raise
        except (HttpLib2Error, socket.gaierror, TimeoutError, ConnectionError) as e:
            network_retries += 1
            if network_retries > 5:
                sys.exit(
                    f"\n網路連續斷線 {network_retries} 次，先檢查網路 / VPN。\n"
                    "已上傳的都記在 .publish_state.json，網路好了重跑同一條指令會接著傳。"
                )
            wait = 10 * network_retries
            print(f"    網路暫時斷線，{wait} 秒後重試（第 {network_retries} 次）: {e}")
            time.sleep(wait)
            continue
    return response["id"]


def upload_all(files: list, state_file: Path, secrets_dir: Path) -> list:
    state = load_state(state_file)
    videos = state.setdefault("videos", {})

    todo = [f for f in files if f.name not in videos]
    if todo:
        youtube = get_authenticated_service(secrets_dir)
    else:
        print("所有影片都已上傳過，跳過上傳。")
        youtube = None

    for i, f in enumerate(files, 1):
        if f.name in videos:
            continue
        print(f"[{i}/{len(files)}] 上傳中: {f.name}")
        video_id = upload_video(youtube, f, f.name)
        videos[f.name] = video_id
        save_state(state_file, state)   # 每支都存，中斷重跑不會重傳
        print(f"    完成: https://youtu.be/{video_id}")

    return [(f.name, videos[f.name]) for f in files]


# ---------------------------------------------------------------- HTML 產生

def build_video_page(title: str, subtitle: str, entries: list) -> str:
    links = "\n".join(
        f'<p><a href = "https://youtu.be/{video_id}">{name}</a></p>'
        for name, video_id in entries
    )
    return f"""<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />

<h2>{title}</h2>
<p><b>{subtitle}</b></p>

{links}

<p><a href = "{SITE_URL}">回主頁</a></p>
"""


def build_index_block(title: str, slug: str, subtitle: str,
                      album_url: str, thumb_url: str, thumb_alt: str) -> str:
    return (
        f'<p><img src="{thumb_url}" alt="{thumb_alt}">'
        f'<a href = "{album_url}"> {title} 照片</a>'
        f'<a> ({subtitle})</a></p>\n'
        f'<p><a href = "{SITE_URL}{slug}.html">{title} 影片</a></p>'
    )


def update_index(index_path: Path, block: str, slug: str) -> str:
    """把 block 插到標題底下；同一個 slug 已存在就取代，讓整支 script 可以重跑。"""
    text = index_path.read_text(encoding="utf-8")

    # 找出既有的兩行區塊（照片行 + 影片行），影片行以 slug 辨識
    pattern = re.compile(
        r"(?:<p><img[^\n]*\n)?<p><a href = \"" + re.escape(f"{SITE_URL}{slug}.html") + r"\"[^\n]*</p>"
    )
    if pattern.search(text):
        print(f"index.html 裡已經有 {slug}，直接取代該區塊。")
        return pattern.sub(lambda _: block, text, count=1)

    if INDEX_ANCHOR not in text:
        sys.exit(f"index.html 裡找不到錨點 {INDEX_ANCHOR}，請手動檢查")
    return text.replace(INDEX_ANCHOR, f"{INDEX_ANCHOR}\n\n{block}\n", 1)


# ---------------------------------------------------------------- git

def git(repo: Path, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check, capture_output=True, text=True,
    )


def git_prepare(repo: Path) -> None:
    dirty = git(repo, "status", "--porcelain").stdout.strip()
    if dirty:
        sys.exit(f"repo 有未 commit 的變更，先處理掉再跑：\n{dirty}")
    print("git pull --rebase ...")
    r = git(repo, "pull", "--rebase", check=False)
    if r.returncode != 0:
        sys.exit(f"git pull 失敗：\n{r.stderr}")


def git_commit(repo: Path, files: list, message: str, push: bool) -> None:
    git(repo, "add", *[str(f) for f in files])
    if not git(repo, "status", "--porcelain").stdout.strip():
        print("沒有任何變更需要 commit。")
        return
    git(repo, "commit", "-m", message)
    print(f"已 commit: {message}")
    if push:
        r = git(repo, "push", check=False)
        if r.returncode != 0:
            sys.exit(f"git push 失敗：\n{r.stderr}")
        print("已 push。")
    else:
        print("（沒有加 --push，尚未推上去）")


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(
        description="每月：上傳影片到 YouTube、產生影片頁、更新 index.html",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("month", help="月資料夾名稱，例如 '20260519 - 20260616'（也接受完整路徑）")
    parser.add_argument("--album-url", help="Google Photos 相簿連結")
    parser.add_argument("--thumb-url", help="縮圖連結（lh3.googleusercontent.com/...=w100）")
    parser.add_argument("--thumb-alt", help="縮圖 alt 文字，預設用月份區間")
    parser.add_argument("--title", help="覆寫標題，預設從資料夾名推導")
    parser.add_argument("--subtitle", help="覆寫年齡字串，預設從生日自動算")
    parser.add_argument("--only", help="只跑某些步驟，逗號分隔: upload,page,index")
    parser.add_argument("--photo-root", type=Path, default=PHOTO_ROOT)
    parser.add_argument("--repo", type=Path, default=REPO_DIR)
    parser.add_argument("--secrets-dir", type=Path,
                        help="client_secret.json / token.json 的位置，預設同 --photo-root")
    parser.add_argument("--push", action="store_true", help="commit 後直接 push")
    parser.add_argument("--dry-run", action="store_true", help="只檢查與列出，不上傳也不改檔")
    args = parser.parse_args()

    steps = {s.strip() for s in args.only.split(",")} if args.only else {"upload", "page", "index"}
    unknown = steps - {"upload", "page", "index"}
    if unknown:
        sys.exit(f"--only 只接受 upload/page/index，不認得: {', '.join(sorted(unknown))}")

    repo = args.repo
    secrets_dir = args.secrets_dir or args.photo_root
    month_dir = resolve_month_dir(args.month, args.photo_root)

    title, slug, end_date = parse_folder_name(month_dir.name)
    if args.title:
        title = args.title
        slug = slug or re.sub(r"\s*-\s*", "_", title).replace(" ", "_")
    if slug is None:
        sys.exit(f"資料夾名稱 '{month_dir.name}' 不是日期區間，請用 --title 指定")

    subtitle = args.subtitle
    if not subtitle:
        if end_date is None:
            sys.exit("無法從資料夾名推算年齡，請用 --subtitle 指定")
        subtitle = build_subtitle(end_date)

    video_dir = month_dir / VIDEO_SUBDIR
    if not video_dir.is_dir():
        sys.exit(f"找不到影片資料夾: {video_dir}")
    order_file = video_dir / ORDER_FILENAME
    state_file = video_dir / STATE_FILENAME
    page_path = repo / f"{slug}.html"
    index_path = repo / "index.html"

    print(f"月資料夾 : {month_dir}")
    print(f"標題     : {title}")
    print(f"影片頁   : {page_path.name}")
    print(f"年齡     : {subtitle}" + ("" if args.subtitle else "  （自動計算）"))
    print()

    files = read_order_file(order_file, video_dir)
    if not files:
        sys.exit("順序檔是空的")

    if "index" in steps:
        if not args.album_url or not args.thumb_url:
            sys.exit("更新 index.html 需要 --album-url 與 --thumb-url"
                     "（或用 --only upload,page 先跳過這步）")

    if args.dry_run:
        print(f"預計依序上傳 {len(files)} 支影片：")
        for i, f in enumerate(files, 1):
            print(f"  {i:3d}. {f.name}")
        already = load_state(state_file).get("videos", {})
        if already:
            print(f"\n其中 {sum(1 for f in files if f.name in already)} 支已經上傳過，會跳過。")
        if page_path.exists():
            print(f"\n注意：{page_path.name} 已經存在，正式跑會覆蓋。")
        print("\n(--dry-run：沒有上傳、沒有修改任何檔案)")
        return

    if page_path.exists() and "page" in steps:
        ans = input(f"{page_path.name} 已經存在，要覆蓋嗎？[y/N] ").strip().lower()
        if ans != "y":
            sys.exit("已中止。")

    git_prepare(repo)

    if "upload" in steps:
        entries = upload_all(files, state_file, secrets_dir)
    else:
        videos = load_state(state_file).get("videos", {})
        miss = [f.name for f in files if f.name not in videos]
        if miss:
            sys.exit(f"跳過上傳，但有 {len(miss)} 支影片還沒有 video id，先跑 upload。")
        entries = [(f.name, videos[f.name]) for f in files]

    changed = []

    if "page" in steps:
        page_path.write_text(build_video_page(title, subtitle, entries), encoding="utf-8")
        print(f"\n已產生 {page_path}，共 {len(entries)} 支影片")
        changed.append(page_path)

    if "index" in steps:
        block = build_index_block(
            title, slug, subtitle, args.album_url, args.thumb_url,
            args.thumb_alt or title,
        )
        index_path.write_text(update_index(index_path, block, slug), encoding="utf-8")
        print(f"已更新 {index_path}")
        changed.append(index_path)

    if changed:
        git_commit(repo, changed, f"Add {title}", args.push)


if __name__ == "__main__":
    main()
