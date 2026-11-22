package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/nsgo-dev/nsgo"
	"github.com/nsgo-dev/nsgo/core"
	"github.com/nsgo-dev/nsgo/ui"
	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/disk"
	"github.com/shirou/gopsutil/v4/host"
	"github.com/shirou/gopsutil/v4/mem"
	"gopkg.in/natefinch/lumberjack.v2"
)

const timestampFormat = "2006-01-02 15:04:05"

type config struct {
	URL string `json:"SERVER_URL"`
	Key string `json:"SERVER_KEY"`
}

func loadConfig(path string) (config, error) {
	var settings config
	raw, err := os.ReadFile(path)
	if err != nil {
		return settings, err
	}
	if err := json.Unmarshal(raw, &settings); err != nil {
		return settings, err
	}
	return settings, settings.validate()
}

func (settings config) validate() error {
	endpoint, err := url.Parse(settings.URL)
	if err != nil || endpoint.Hostname() == "" || (endpoint.Scheme != "http" && endpoint.Scheme != "https") {
		return fmt.Errorf("请输入以 http:// 或 https:// 开头的完整服务器地址")
	}
	return nil
}

func saveConfig(path string, settings config) error {
	if err := settings.validate(); err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".client-*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	encoder := json.NewEncoder(file)
	encoder.SetIndent("", "  ")
	writeErr := encoder.Encode(settings)
	closeErr := file.Close()
	if writeErr != nil {
		return writeErr
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(file.Name(), path)
}

func rounded(value float64) float64 { return math.Round(value*100) / 100 }
func gib(size uint64) float64       { return rounded(float64(size) / (1 << 30)) }

func collect(ctx context.Context) (map[string]any, error) {
	usage, err := cpu.PercentWithContext(ctx, time.Second, false)
	if err != nil {
		return nil, err
	}
	if len(usage) == 0 {
		return nil, fmt.Errorf("CPU 采样为空")
	}
	memory, err := mem.VirtualMemoryWithContext(ctx)
	if err != nil {
		return nil, err
	}
	drive, err := disk.UsageWithContext(ctx, `C:\`)
	if err != nil {
		return nil, err
	}
	hostname, err := os.Hostname()
	if err != nil {
		return nil, err
	}
	addresses, err := net.DefaultResolver.LookupIP(ctx, "ip4", hostname)
	if err != nil {
		return nil, err
	}
	if len(addresses) == 0 {
		return nil, fmt.Errorf("无法获取本机 IPv4 地址")
	}
	boot, err := host.BootTimeWithContext(ctx)
	if err != nil {
		return nil, err
	}
	now := time.Now()
	return map[string]any{
		"hostname": hostname, "local_ip": addresses[0].String(), "timestamp": now.Format(timestampFormat),
		"cpu_percent": rounded(usage[0]), "memory_total_gb": gib(memory.Total),
		"memory_used_gb": gib(memory.Total - memory.Available), "memory_percent": rounded(memory.UsedPercent),
		"disk_total_gb": gib(drive.Total), "disk_used_gb": gib(drive.Used), "disk_percent": rounded(drive.UsedPercent),
		"boot_time": time.Unix(int64(boot), 0).Format(timestampFormat), "uptime_seconds": now.Unix() - int64(boot),
	}, nil
}

func report(ctx context.Context, client *http.Client, settings config) error {
	data, err := collect(ctx)
	if err != nil {
		return err
	}
	body, err := json.Marshal(map[string]any{"key": settings.Key, "data": data})
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, settings.URL, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	response, err := client.Do(req)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("服务器返回 %s", response.Status)
	}
	_, err = io.Copy(io.Discard, io.LimitReader(response.Body, 64<<10))
	return err
}

func monitor(ctx context.Context, settings config, update func(string)) {
	client := &http.Client{Timeout: 10 * time.Second}
	defer client.CloseIdleConnections()
	for ctx.Err() == nil {
		message := "上报成功"
		if err := report(ctx, client, settings); err != nil {
			message = "上报失败 · " + err.Error()
		}
		log.Print(message)
		update(time.Now().Format(timestampFormat) + "\n" + message)
		select {
		case <-ctx.Done():
			return
		case <-time.After(15 * time.Minute):
		}
	}
}

func main() {
	executable, err := os.Executable()
	if err != nil {
		log.Fatal(err)
	}
	dir := filepath.Dir(executable)
	path := flag.String("set", filepath.Join(dir, "client.set"), "用户设定文件（JSON）")
	headless := flag.Bool("headless", false, "无状态窗口运行")
	flag.Parse()
	logfile := &lumberjack.Logger{Filename: filepath.Join(dir, "monitor_client.log"), MaxSize: 10, MaxBackups: 3}
	defer logfile.Close()
	log.SetOutput(io.MultiWriter(logfile, os.Stderr))
	settings, configErr := loadConfig(*path)
	if *headless {
		if configErr != nil {
			log.Fatal(configErr)
		}
		monitor(context.Background(), settings, func(string) {})
		return
	}
	nsgo.Run(func(scope *core.Scope) core.Node {
		scope.SetTitle("Nexus Monitor")
		endpoint := core.NewSignal(scope, settings.URL)
		key := core.NewSignal(scope, settings.Key)
		notice := core.NewSignal(scope, "")
		status := core.NewSignal(scope, "尚未开始上报")
		var stop context.CancelFunc
		start := func(active config) {
			if stop != nil {
				stop()
			}
			ctx, cancel := context.WithCancel(scope.Context())
			stop = cancel
			status.Set("正在采集系统状态…")
			go monitor(ctx, active, func(message string) {
				scope.Post(func() {
					if ctx.Err() == nil {
						status.Set(message)
					}
				})
			})
		}
		if configErr == nil {
			start(settings)
		} else {
			notice.Set("请填写参数或导入 .set 文件")
		}
		pick := func() {
			scope.PickFiles(core.PickFilesOptions{Title: "导入客户端设定", Types: []string{".set", ".json"}, TypesName: "客户端设定"}, func(files []core.File, err error) {
				if err != nil {
					notice.Set("导入失败：" + err.Error())
					return
				}
				if len(files) == 0 {
					return
				}
				draft, err := loadConfig(files[0].Path)
				if err != nil {
					notice.Set("导入失败：" + err.Error())
					return
				}
				endpoint.Set(draft.URL)
				key.Set(draft.Key)
				notice.Set("已导入 " + files[0].Name + "，点击保存并应用后生效")
			})
		}
		apply := func() {
			draft := config{URL: strings.TrimSpace(endpoint.Get()), Key: key.Get()}
			if err := saveConfig(*path, draft); err != nil {
				notice.Set("保存失败：" + err.Error())
				return
			}
			endpoint.Set(draft.URL)
			notice.Set("配置已保存并生效")
			start(draft)
		}
		field := core.Style{Height: core.Pt(32), Padding: core.Symmetric(4, 8)}
		return core.View{Style: core.Style{Padding: core.All(20), Gap: 12}, Children: []core.Node{
			core.Text{Content: "服务器上报地址"},
			ui.Field{Value: endpoint.Get, OnChange: endpoint.Set, Placeholder: "http://服务器:9000/api/status", Style: field},
			core.Text{Content: "通信密钥"},
			ui.Field{Value: key.Get, OnChange: key.Set, Masked: true, Style: field},
			core.View{Style: core.Style{Direction: core.Row, Gap: 8}, Children: []core.Node{
				ui.Button{Text: "导入 .set 文件", OnPress: pick, Variant: ui.ButtonSecondary},
				ui.Button{Text: "保存并应用", OnPress: apply},
			}},
			core.Text{ContentFunc: notice.Get},
			core.Text{ContentFunc: func() string { return status.Get() }},
		}}
	}, nsgo.WindowSize(540, 330))
}
