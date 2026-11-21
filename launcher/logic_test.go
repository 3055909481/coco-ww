package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCompareVersion(t *testing.T) {
	cases := []struct {
		a, b string
		want int
	}{
		{"v1.0.5", "v1.0.4", 1},
		{"v1.0.5", "v1.0.5", 0},
		{"v1.0.4", "v1.0.5", -1},
		{"v1.0.10", "v1.0.9", 1}, // 数字段不能按字符串比
		{"1.2.0", "v1.1.9", 1},
		{"v1.0.5-beta", "v1.0.5", 0}, // 预发布后缀不参与数值比较
		{"v2.0", "v1.99.99", 1},
	}
	for _, c := range cases {
		got := compareVersion(c.a, c.b)
		if got != c.want {
			t.Errorf("compareVersion(%q,%q) = %d, want %d", c.a, c.b, got, c.want)
		}
	}
}

func TestVersionParts(t *testing.T) {
	if got := versionParts("v1.0.5"); len(got) != 3 || got[0] != 1 || got[2] != 5 {
		t.Errorf("versionParts: %v", got)
	}
}

func TestHasNonASCII(t *testing.T) {
	if hasNonASCII(`D:\coco-ww`) {
		t.Error("纯英文路径被误判")
	}
	if !hasNonASCII(`D:\游戏\coco-ww`) {
		t.Error("中文路径未被识别")
	}
}

func TestReadLocalVersion(t *testing.T) {
	dir := t.TempDir()
	// 优先 version.txt
	if err := os.WriteFile(filepath.Join(dir, "version.txt"), []byte("v2.3.4\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if got := readLocalVersion(dir); got != "v2.3.4" {
		t.Errorf("version.txt: got %q", got)
	}
	// 回退 config.py
	dir2 := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir2, "config.py"), []byte("# c\nversion = \"v1.0.5\"\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if got := readLocalVersion(dir2); got != "v1.0.5" {
		t.Errorf("config.py: got %q", got)
	}
}

func TestRequireFiles(t *testing.T) {
	dir := t.TempDir()
	missing := requireFiles(dir)
	if len(missing) != 3 {
		t.Errorf("空目录应缺 3 个文件, got %v", missing)
	}
	// 建齐后应为 0
	for _, rel := range []string{"python/pythonw.exe", "main.pyc", "version.txt"} {
		p := filepath.Join(dir, rel)
		os.MkdirAll(filepath.Dir(p), 0755)
		os.WriteFile(p, []byte("x"), 0644)
	}
	if got := requireFiles(dir); len(got) != 0 {
		t.Errorf("应齐全, got %v", got)
	}
}
