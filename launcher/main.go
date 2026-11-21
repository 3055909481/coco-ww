package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime/debug"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

var (
	kernel32 = syscall.NewLazyDLL("kernel32.dll")
	shell32  = syscall.NewLazyDLL("shell32.dll")
	user32   = syscall.NewLazyDLL("user32.dll")

	procIsUserAnAdmin       = shell32.NewProc("IsUserAnAdmin")
	procCreateMutexW        = kernel32.NewProc("CreateMutexW")
	procMessageBoxW         = user32.NewProc("MessageBoxW")
	procShellExecuteW       = shell32.NewProc("ShellExecuteW")
	procGetModuleFileNameW  = kernel32.NewProc("GetModuleFileNameW")
)

const (
	mbOK              = 0x00000000
	mbYesNo           = 0x00000004
	mbIconInformation = 0x00000040
	mbIconWarning     = 0x00000030
	mbIconError       = 0x00000010
	idYes             = 6
	errorAlreadyExists = 183
)

func utf16ptr(s string) *uint16 {
	p, err := syscall.UTF16PtrFromString(s)
	if err != nil {
		return nil
	}
	return p
}

func messageBox(text, title string, flags uint32) int {
	ret, _, _ := procMessageBoxW.Call(0, uintptr(unsafe.Pointer(utf16ptr(text))),
		uintptr(unsafe.Pointer(utf16ptr(title))), uintptr(flags))
	return int(ret)
}

func isAdmin() bool {
	ret, _, _ := procIsUserAnAdmin.Call()
	return ret != 0
}

func exePath() string {
	buf := make([]uint16, syscall.MAX_PATH+1)
	n, _, _ := procGetModuleFileNameW.Call(0, uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)))
	if n == 0 {
		return ""
	}
	return syscall.UTF16ToString(buf[:n])
}

// shellExecute 用 runas 提权或打开 URL。返回是否成功（>32 表示成功）。
func shellExecute(verb, file, params, dir string, show int) bool {
	ret, _, _ := procShellExecuteW.Call(
		0,
		uintptr(unsafe.Pointer(utf16ptr(verb))),
		uintptr(unsafe.Pointer(utf16ptr(file))),
		uintptr(unsafe.Pointer(utf16ptr(params))),
		uintptr(unsafe.Pointer(utf16ptr(dir))),
		uintptr(show),
	)
	return ret > 32
}

// acquireSingleInstance 返回 true 表示拿到锁；false 表示已有一个实例在跑。
func acquireSingleInstance() bool {
	name := utf16ptr("Global\\coco-ww-launcher-single-instance")
	handle, _, _ := procCreateMutexW.Call(0, 0, uintptr(unsafe.Pointer(name)))
	if handle == 0 {
		return true // 创建失败不该阻止启动
	}
	// CreateMutexW 后紧接调用 GetLastError；syscall.GetLastError 返回 error 接口
	errno, ok := syscall.GetLastError().(syscall.Errno)
	return !ok || uint32(errno) != errorAlreadyExists
}

func main() {
	// 兜底：任何 panic 都弹窗，不静默闪退
	defer func() {
		if r := recover(); r != nil {
			messageBox(fmt.Sprintf("启动器内部错误：%v\n\n%s", r, debug.Stack()), "coco-ww 启动器", mbOK|mbIconError)
		}
	}()

	exe := exePath()
	appDir := filepath.Dir(exe)
	if exe == "" || appDir == "" {
		messageBox("无法确定启动器所在目录。", "coco-ww 启动器", mbOK|mbIconError)
		return
	}

	// UAC 提权：游戏以管理员运行，coco-ww 也必须提权才能发送按键
	if !isAdmin() {
		if !shellExecute("runas", exe, strings.Join(os.Args[1:], " "), appDir, 1) {
			messageBox("需要管理员权限才能启动。\n请在弹出的 UAC 提示中点击“是”。", "coco-ww 启动器", mbOK|mbIconWarning)
		}
		return
	}

	// 单实例：防止多个实例同时发按键
	if !acquireSingleInstance() {
		messageBox("coco-ww 已经在运行了。", "coco-ww 启动器", mbOK|mbIconInformation)
		return
	}

	// 中文路径：ok-script 框架硬性要求纯英文路径
	if hasNonASCII(appDir) {
		messageBox("安装路径包含中文，程序会启动失败。\n请把整个文件夹移到纯英文路径（例如 D:\\coco-ww）再运行。\n\n当前路径："+appDir,
			"coco-ww 启动器", mbOK|mbIconError)
		return
	}

	// 必需文件校验
	if missing := requireFiles(appDir); len(missing) > 0 {
		messageBox("安装不完整，缺少这些文件：\n  - "+strings.Join(missing, "\n  - ")+
			"\n\n请重新下载完整包 coco-ww-vX.Y.Z.zip 并解压。", "coco-ww 启动器", mbOK|mbIconError)
		return
	}

	// 更新检查（只读 GitHub API，不自行替换文件；网络失败静默跳过）
	current := readLocalVersion(appDir)
	if newer, url, latest := checkUpdate(current); newer && url != "" {
		ans := messageBox(fmt.Sprintf("发现新版本 %s（当前 %s）。\n是否打开下载页面？", latest, current),
			"coco-ww 启动器", mbYesNo|mbIconInformation)
		if ans == idYes {
			shellExecute("open", url, "", "", 1)
		}
	}

	// 启动主程序：pythonw.exe -X utf8 main.pyc
	pythonw := filepath.Join(appDir, "python", "pythonw.exe")
	cmd := exec.Command(pythonw, "-X", "utf8", "main.pyc")
	cmd.Dir = appDir
	cmd.Stdout = nil
	cmd.Stderr = nil
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x00000008 | 0x00000200} // DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
	if err := cmd.Start(); err != nil {
		messageBox("启动主程序失败："+err.Error(), "coco-ww 启动器", mbOK|mbIconError)
		return
	}
	// 启动器使命完成，立即退出（主程序独立运行）
	time.Sleep(300 * time.Millisecond) // 给主程序一点时间起来，避免 mutex 立刻释放造成误判
}
