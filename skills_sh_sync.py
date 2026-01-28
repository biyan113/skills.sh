#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
skills_sh_sync.py

功能：同步 skills.sh 网站的技能列表（All Time、Trending、Hot），保存为 CSV 和 JSON。

- 抓取页面：
  * https://skills.sh/
  * https://skills.sh/trending
  * https://skills.sh/hot
- 解析字段：rank, skill_name, owner_repo, installs, page_url, category
- 输出目录：/home/user/workspace/skills_sh/
  * skills_sh_list_<category>.csv
  * skills_sh_list_<category>.json

若页面为前端渲染导致解析失败，脚本会回退为基于文本的解析策略。
"""
import os
import re
import json
import csv
from datetime import datetime
from typing import List, Dict

import requests
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0 Safari/537.36"
}

CATEGORIES = {
    "all_time": "https://skills.sh/",
    "trending": "https://skills.sh/trending",
    "hot": "https://skills.sh/hot",
}


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def fetch(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_html(html: str, base_url: str, category: str) -> List[Dict]:
    """优先用 HTML 结构解析；若失败，抛异常让上层回退到文本解析。"""
    soup = BeautifulSoup(html, "html.parser")

    # 经验规则：页面包含“Skills Leaderboard”，列表项通常为 <a href="/owner/repo/skill"> 文本里带安装量
    leaderboard = []

    # 尝试查找所有链接，筛选符合 /<owner>/<repo>/<skill> 形式的
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # 规范化绝对链接
        if href.startswith("/"):
            page_url = f"https://skills.sh{href}"
        elif href.startswith("http"):
            page_url = href
        else:
            continue

        # 匹配技能链接路径模式
        m = re.match(r"^https?://skills\.sh/([^/]+)/([^/]+)/([^/?#]+)$", page_url)
        if not m:
            continue
        owner, repo, skill = m.groups()

        # 从链接文本或父节点中提取安装量与排名（如果可用）
        text = " ".join(a.stripped_strings)
        installs = None
        rank = None
        name = None

        # 名称统一使用 slug，避免误抓数字等噪声
        name = skill

        # 安装量形如 "61.0K" 或数字
        install_match = re.search(r"(\d+[.,]?\d*[KkMm]?|\d+)", text)
        if install_match:
            installs = install_match.group(1)

        # 排名可能在链接前生成的序号，如 "1 ###" 等（HTML中不一定拿得到）
        # 尝试从前后文抓取序号
        parent_text = " ".join(a.parent.stripped_strings) if a.parent else text
        rank_match = re.search(r"\b(\d{1,3})\b", parent_text)
        if rank_match:
            try:
                rank = int(rank_match.group(1))
            except:
                rank = None

        leaderboard.append({
            "rank": rank,
            "skill_name": name,
            "owner_repo": f"{owner}/{repo}",
            "installs": installs,
            "page_url": page_url,
            "category": category,
        })

    if not leaderboard:
        raise ValueError("HTML解析未得到任何技能项，回退到文本解析。")

    # 去重：按 page_url 唯一
    dedup = {}
    for item in leaderboard:
        dedup[item["page_url"]] = item
    rows = list(dedup.values())

    # 简单质量校验：安装量若大多为极小整数（如 1、2…），判定质量较差
    small_installs = 0
    for r in rows:
        if r["installs"] is not None and re.fullmatch(r"\d{1,2}", str(r["installs"])):
            small_installs += 1
    if rows and small_installs / len(rows) > 0.5:
        raise ValueError("HTML解析质量差（安装量多为小整数），回退到文本解析。")

    return rows


def parse_text_fallback(text: str, base_url: str, category: str) -> List[Dict]:
    """
    回退解析：针对 Markdown/纯文本（例如抓取工具产出的页面文本）
    识别形如：
    [1 ### vercel-react-best-practices vercel-labs/agent-skills 61.0K](https://skills.sh/vercel-labs/agent-skills/vercel-react-best-practices)
    """
    items = []
    # 正则：抓取 [前缀文本](链接)
    pattern = re.compile(r"\[(.*?)\]\((https?://skills\.sh/[^)]+)\)")
    for m in pattern.finditer(text):
        prefix = m.group(1)
        url = m.group(2)
        # 解析 prefix 的结构："1 ### name owner/repo installs" 或 "name owner/repo installs"
        # 提取 rank（可选）、name、owner/repo、installs
        rank = None
        installs = None
        owner_repo = None
        name = None

        # 尝试匹配末尾的安装量，如 61.0K 或数字
        installs_match = re.search(r"(\d+[.,]?\d*[KkMm]?|\d+)$", prefix)
        if installs_match:
            installs = installs_match.group(1)

        # owner/repo 格式
        owner_repo_match = re.search(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", prefix)
        if owner_repo_match:
            owner_repo = f"{owner_repo_match.group(1)}/{owner_repo_match.group(2)}"

        # rank 在开头的数字
        rank_match = re.match(r"^(\d{1,3})\b", prefix)
        if rank_match:
            try:
                rank = int(rank_match.group(1))
            except:
                rank = None

        # skill 名称：链接路径最后一段
        skill_slug = url.rstrip("/").split("/")[-1]
        # 名称优先用 slug，其次用 prefix 的第一个非数字词
        name = skill_slug

        items.append({
            "rank": rank,
            "skill_name": name,
            "owner_repo": owner_repo,
            "installs": installs,
            "page_url": url,
            "category": category,
        })

    # 去重
    dedup = {i["page_url"]: i for i in items}
    return list(dedup.values())


def normalize_rows(rows: List[Dict]) -> List[Dict]:
    norm = []
    for r in rows:
        # 排名字段在静态抓取中不可准确获取，统一置空
        r["rank"] = None
        installs = r.get("installs")
        # 有效安装量格式：包含 K/M 或者是较大的数字（>=1000）
        valid = False
        if installs is None:
            valid = False
        else:
            s = str(installs)
            if re.search(r"[KkMm]", s):
                valid = True
            else:
                try:
                    n = int(re.sub(r"[,.]", "", s))
                    if n >= 1000:
                        valid = True
                except:
                    valid = False
        if not valid:
            r["installs"] = None
        # 若 skill_name 或 owner_repo 缺失，用 URL 兜底
        if not r.get("skill_name") and r.get("page_url"):
            r["skill_name"] = r["page_url"].rstrip("/").split("/")[-1]
        if not r.get("owner_repo") and r.get("page_url"):
            parts = r["page_url"].split("/")
            if len(parts) >= 6:
                r["owner_repo"] = f"{parts[3]}/{parts[4]}"
        norm.append(r)
    return norm

def save_outputs(category: str, rows: List[Dict]):
    ensure_dir(BASE_DIR)
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # JSON
    json_path = os.path.join(BASE_DIR, f"skills_sh_list_{category}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"timestamp": ts, "count": len(rows), "rows": rows}, f, ensure_ascii=False, indent=2)

    # CSV
    csv_path = os.path.join(BASE_DIR, f"skills_sh_list_{category}.csv")
    fieldnames = ["rank", "skill_name", "owner_repo", "installs", "page_url", "category"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print(f"Saved {len(rows)} rows -> {json_path} , {csv_path}")


def sync_category(category: str, url: str):
    print(f"Syncing {category} from {url}")
    html = fetch(url)
    try:
        rows = parse_html(html, url, category)
    except Exception as e:
        print(f"HTML解析失败或质量差：{e}；尝试文本回退解析…")
        # 从 HTML 提取纯文本后使用回退解析
        try:
            soup = BeautifulSoup(html, "html.parser")
            plain_text = soup.get_text("\n")
        except Exception:
            plain_text = html
        rows = parse_text_fallback(plain_text, url, category)
    rows = normalize_rows(rows)
    save_outputs(category, rows)


def main():
    ensure_dir(BASE_DIR)
    for cat, url in CATEGORIES.items():
        try:
            sync_category(cat, url)
        except Exception as e:
            print(f"[ERROR] 同步 {cat} 时失败：{e}")

    print("Done.")


if __name__ == "__main__":
    main()
