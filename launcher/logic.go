package main

import (
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

// repo 由构建脚本用 -ldflags "-X main.repo=owner/repo" 注入。
var repo = "3055909481/coco-ww"

const httpTimeout = 8 * time.Second

// 路径含中文时 ok-script 框架会启动失败（README 的硬性要求）。
func hasNonASCII(path string) bool {
	for _, r := range path {
		if r > 127 {
			return true
		}
	}
	return false
}

// 版本号比较：v1.0.5 / 1.0.5 / v1.0.5-beta 都能比，纯数字段用整数比较避免 1.0.10 < 1.0.9 的坑。
func compareVersion(a, b string) int {
	pa, pb := versionParts(a), versionParts(b)
	n := len(pa)
	if len(pb) > n {
		n = len(pb)
	}
	for i := 0; i < n; i++ {
		var x, y int
		if i < len(pa) {
			x = pa[i]
		}
		if i < len(pb) {
			y = pb[i]
		}
		if x < y {
			return -1
		}
		if x > y {
			return 1
		}
	}
	return 0
}

func versionParts(v string) []int {
	v = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(v), "v"))
	// 预发布后缀（-beta 等）不参与数值比较，只比主版本段
	if i := strings.IndexAny(v, "-+"); i >= 0 {
		v = v[:i]
	}
	parts := strings.Split(v, ".")
	out := make([]int, 0, len(parts))
	for _, p := range parts {
		n, err := strconv.Atoi(strings.TrimSpace(p))
		if err != nil {
			n = 0
		}
		out = append(out, n)
	}
	return out
}

// 读取本地版本：优先 version.txt（混淆整包），回退 config.py 文本（源码开发版）。
func readLocalVersion(appDir string) string {
	if data, err := os.ReadFile(filepath.Join(appDir, "version.txt")); err == nil {
		if v := strings.TrimSpace(string(data)); v != "" {
			return v
		}
	}
	if data, err := os.ReadFile(filepath.Join(appDir, "config.py")); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "version") && strings.Contains(line, "=") {
				val := strings.TrimSpace(strings.SplitN(line, "=", 2)[1])
				val = strings.Trim(val, "\"' \t")
				if val != "" {
					return val
				}
			}
		}
	}
	return ""
}

// 检查 GitHub 上是否有比本地更新的版本。
// 走 raw.githubusercontent.com 的 version.txt（不计入 GitHub API 配额，不会被限流），
// 网络失败一律视为"没有更新"，不阻塞启动。
func checkUpdate(current string) (newer bool, url string, latest string) {
	if repo == "" || !strings.Contains(repo, "/") {
		return false, "", ""
	}
	rawURL := fmt.Sprintf("https://raw.githubusercontent.com/%s/obfuscated/version.txt", repo)
	client := &http.Client{Timeout: httpTimeout}
	req, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		return false, "", ""
	}
	req.Header.Set("User-Agent", "coco-ww-launcher")
	resp, err := client.Do(req)
	if err != nil {
		return false, "", ""
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return false, "", ""
	}
	buf := make([]byte, 128)
	n, _ := resp.Body.Read(buf)
	tag := strings.TrimSpace(string(buf[:n]))
	if tag == "" || len(tag) > 40 {
		return false, "", ""
	}
	if compareVersion(tag, current) > 0 {
		return true, fmt.Sprintf("https://github.com/%s/releases/latest", repo), tag
	}
	return false, "", tag
}

// require 校验整包必需的文件是否齐全，返回缺失项的描述列表。
func requireFiles(appDir string) []string {
	var missing []string
	if !hasNonASCII(appDir) {
		// 路径检查单独在 main 里提示（中文路径不是"文件缺失"）
	}
	for _, rel := range []string{filepath.Join("python", "pythonw.exe"), "main.pyc", "version.txt"} {
		if _, err := os.Stat(filepath.Join(appDir, rel)); err != nil {
			missing = append(missing, rel)
		}
	}
	return missing
}

// sortedStrings 仅用于测试里稳定输出。
func sortedStrings(in []string) []string {
	out := append([]string(nil), in...)
	sort.Strings(out)
	return out
}
