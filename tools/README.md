# 每月相簿發佈流程

一支 script 做完三件事：上傳影片到 YouTube → 產生該月影片頁 → 更新 `index.html` → commit。

---

## 一、一次性設定（只做一次）

### 1. 建 venv 並安裝套件

**一定要用 venv。** Homebrew 的 Python 每次升級大版本（例如 3.13 → 3.14）都會把舊版的 `site-packages` 清掉，套件就這樣消失；而且 Homebrew Python 也擋掉直接 `pip3 install`（externally-managed）。

```bash
python3 -m venv ~/Desktop/photo/.venv
~/Desktop/photo/.venv/bin/python3 -m pip install google-api-python-client google-auth-oauthlib
```

裝好之後就不用再管 venv 了：**一律用 `./tools/publish-month` 這個包裝來跑**，它會自動挑 venv 裡的 python。

```bash
cd ~/Desktop/photo/photo_video
./tools/publish-month "20260519 - 20260616" --dry-run
```

> 不要直接用 `python3 tools/publish_month.py` —— 那會用到系統 python，沒裝套件，跑到上傳才會失敗。

想在任何目錄下都能跑的話，加進 `~/.zshrc`：

```bash
alias publish-month='~/Desktop/photo/photo_video/tools/publish-month'
```

### 2. 確認憑證位置

`client_secret.json` 要放在 `~/Desktop/photo/`（也就是 `PHOTO_ROOT`，repo 的上一層）。

這個檔案來自 Google Cloud Console：建立專案 → 啟用 **YouTube Data API v3**（不需要開 billing）→ 憑證 → 建立 OAuth 用戶端 ID → 應用程式類型選「電腦版應用程式」→ 下載 JSON 改名為 `client_secret.json`。

> **這兩個檔案絕對不要進 repo。** `client_secret.json` 和 `token.json` 都已經寫進 `.gitignore`。

### 3. 第一次執行會跳出瀏覽器要你登入授權

授權完會在 `~/Desktop/photo/` 產生 `token.json`，之後都靠它，不用再登入。

### 4. 換一台電腦時

```bash
git clone https://github.com/yckelly/photo_video.git ~/Desktop/photo/photo_video
```

然後把 `client_secret.json` 複製到 `~/Desktop/photo/`，重跑一次授權即可。

---

## 二、每個月的流程

### 步驟 1：整理影片（手動）

在月資料夾底下，把要上傳的影片放進 `傳/影片/`：

```
~/Desktop/photo/20260519 - 20260616/傳/影片/
```

然後在**同一層**建立 `filenames.txt`，**一行一個檔名，順序就是網頁上的顯示順序**：

```
20260519_兄弟比賽迷你高爾夫_IMG_3369-IMG_3371(轉).mov
20260520_Nolan騎平衡車_IMG_2922.MOV
...
```

- 空行和 `#` 開頭的行會被忽略
- **資料夾裡有、但沒列進 `filenames.txt` 的影片不會上傳**，script 只會提醒你一聲，不是錯誤（你刻意挑掉不公開的就是這樣處理）
- 檔名有重複、或列了但檔案不存在，script 會直接擋下來

### 步驟 2：準備兩個連結（手動）

- **相簿連結**：Google Photos 相簿 → 分享 → 複製連結，長得像 `https://photos.app.goo.gl/xxxx`
- **縮圖連結**：從相簿挑一張當封面，複製圖片網址，長得像 `https://lh3.googleusercontent.com/pw/AP1Gcz...=w100`
  - 結尾的 `=w100` 是寬度，既有的項目混用 `=w75`（直式照片）和 `=w100`（橫式照片），照你習慣挑

### 步驟 3：先 dry-run 確認

```bash
cd ~/Desktop/photo/photo_video

./tools/publish-month "20260519 - 20260616" \
    --album-url "https://photos.app.goo.gl/xxxx" \
    --thumb-url "https://lh3.googleusercontent.com/pw/AP1Gcz...=w100" \
    --dry-run
```

`--dry-run` **不上傳、不改任何檔案**，只印出：

- 推導出來的標題與影片頁檔名
- 自動算出來的年齡字串 ← **重點確認這行**
- 哪些影片沒被列進順序檔
- 完整的上傳順序（照 `filenames.txt`）
- 該月 html 是否已經存在

### 步驟 4：正式跑

確認沒問題後，拿掉 `--dry-run`、加上 `--push`：

```bash
./tools/publish-month "20260519 - 20260616" \
    --album-url "https://photos.app.goo.gl/xxxx" \
    --thumb-url "https://lh3.googleusercontent.com/pw/AP1Gcz...=w100" \
    --push
```

會依序做：

1. 檢查 repo 沒有未 commit 的變更 → `git pull --rebase`
2. 逐支上傳到 YouTube（設定為**不公開 unlisted**、非兒童內容）
3. 產生 `20260519_20260616.html`
4. 在 `index.html` 最上面插入兩行（照片列 + 影片列）
5. `git commit` 並 `push`

一百支影片大概要跑不少時間，可以放著讓它傳。

---

## 三、參數速查

| 參數 | 說明 |
|---|---|
| `月資料夾名稱` | 唯一的必填參數，例如 `"20260519 - 20260616"`。記得加引號（名稱有空格）|
| `--album-url` | Google Photos 相簿連結 |
| `--thumb-url` | 縮圖連結 |
| `--dry-run` | 只檢查與列出，不上傳、不改檔 |
| `--push` | commit 後直接 push（不加就只 commit，你自己再推）|
| `--subtitle` | 覆寫年齡字串，預設從生日自動算 |
| `--title` | 覆寫標題，預設從資料夾名推導 |
| `--thumb-alt` | 縮圖 alt 文字，預設用月份區間 |
| `--only` | 只跑某些步驟，逗號分隔：`upload,page,index` |
| `--photo-root` | 照片根目錄，預設 `~/Desktop/photo` |
| `--secrets-dir` | 憑證位置，預設同 `--photo-root` |

---

## 四、常見狀況

### `Token has been expired or revoked`

Script 會自動偵測並重新跑一次授權（開瀏覽器），照著點完就會繼續上傳，不用手動刪 `token.json`。

**為什麼會過期**：如果 Google Cloud Console 的「OAuth 同意畫面」還停在 **「測試中 / Testing」** 狀態，refresh token **七天**就會失效。你是每月才跑一次，等於每次都會遇到。

一勞永逸的話，到 Google Cloud Console → OAuth 同意畫面 → 把發布狀態改成 **「正式版 / In production」**。之後 refresh token 就不會固定七天過期。改完第一次授權時可能會看到「這個應用程式未經 Google 驗證」的警告 —— 因為 `youtube.upload` 屬於敏感權限，而這個 app 只有你自己用、沒送審。點「進階」→「繼續」即可。

（不改也行，只是每個月要多點幾下重新授權。）

### 上傳到一半中斷了（每日上限 / 網路斷線）

**直接重跑同一條指令就好。** 每上傳成功一支就會記進月資料夾的 `傳/影片/.publish_state.json`，重跑會自動跳過已傳的，從卡住那支繼續，不會重複上傳。

YouTube 對新頻道有每日上傳支數的隱形上限（不是 API 額度，官方沒公布數字），通常 24 小時內重置。撞到的話 script 會明講，隔天重跑即可。

### 相簿連結還沒生出來，想先傳影片

```bash
./tools/publish-month "20260519 - 20260616" --only upload,page
```

之後連結有了再補：

```bash
./tools/publish-month "20260519 - 20260616" \
    --album-url "..." --thumb-url "..." --only index
```

（`--only index` 會從 `.publish_state.json` 讀已上傳的 video id，不會重傳）

### 年齡算錯 / 想自己寫

```bash
--subtitle "Lucas 八歲四個月 Matt 六歲十一個月 Nolan 三歲五個月"
```

年齡是用**區間結束日**計算、四捨五入到最近的月。生日寫在 `publish_month.py` 的 `KIDS`：Lucas 2018-02-14、Matt 2019-07-19、Nolan 2023-01-26。這個公式對過 `index.html` 全部 78 筆歷史項目，都相符。

### 不是月份區間的資料夾（例如台灣、Costa Rica）

資料夾名不是 `YYYYMMDD - YYYYMMDD` 格式時，沒辦法自動推導，要自己給：

```bash
./tools/publish-month "20250628 Taiwan" \
    --title "20250628 台灣" \
    --subtitle "Lucas 七歲五個月 Matt 六歲零個月 Nolan 二歲六個月" \
    --album-url "..." --thumb-url "..."
```

### 想重做某個月

同一條指令再跑一次就好，是可以重跑的：

- 影片頁存在時會問你要不要覆蓋
- `index.html` 裡已經有同一筆時會**取代**，不會插出重複的項目
- 已上傳的影片不會重傳

### repo 有未 commit 的變更

script 會直接中止並列出來。先自己 commit 或 `git checkout` 掉，再重跑。
