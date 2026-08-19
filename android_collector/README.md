# Android GUI 轨迹数据采集工具

不改任何 App，通过 ADB + uiautomator + getevent，人工操作手机、工具自动记录每一步
截图 / 控件 bbox / 触摸坐标，最终输出需求文档中的 JSON 轨迹格式，截图保存本地。

## 环境准备

1. 安装 Android platform-tools（含 adb）：https://developer.android.com/tools/releases/platform-tools
   - 或用已装的 Android Studio SDK 里的 `platform-tools\adb.exe`
2. 手机开启「开发者选项 -> USB 调试」，USB 连接电脑
3. `adb devices` 能看到设备（第一次授权「允许 USB 调试」）

## 运行

```bash
python -m android_collector.main
```

多台设备时脚本会提示选择。也可指定：

```bash
set ADB_PATH=C:\path\to\adb.exe     # 或把 adb 加入 PATH
python -m android_collector.main
```

## 采集流程

1. 启动脚本，输入：任务指令(goal)、应用名、批次 tag
2. 脚本自动截取起始画面
3. **在手机完成一个动作**，脚本自动检测手势并记录：
   - 截取动作后的画面
   - 从 `getevent` 解析触摸坐标（tap / swipe 自动识别，并按触摸屏分辨率缩放）
   - 从动作前画面的 `uiautomator dump` 匹配被点元素的 bbox
   - 自动推断动作类型（从桌面启动 App -> open；点击 -> click；滑动 -> swipe）
4. **每条动作记录后，在终端即时补写该步骤的 thought**（回车留空）
5. 结束：输入 `end`（自动补 terminate 步并输出 JSON）；退出不保存：`q`
6. 可预写下一步备注：`c:点击会员入口` / `i:123456` / `open:微信`

## 输出

```
output/
└── batch_20260819_143501_task_0532/
    ├── trajectory.json      # 需求文档格式
    └── screenshots/
        ├── step_01.png
        └── ...
```

`trajectory.json` 中的 `oss_url` 为本地相对路径（`batch_xxx/screenshots/step_01.png`），
未上传 OSS，可按需替换为本地绝对路径或后续改传 OSS。

## 回放轨迹

```bash
python -m android_collector.replay output\batch_xxx\trajectory.json [--delay 1.5] [--shot-dir replay_shots]
```

- `--delay`：每步间隔秒数（默认 1.0）
- `--shot-dir`：回放后截图保存目录，用于逐屏比对验证

**重要：MIUI / 部分国产 ROM 默认禁止 adb 注入触摸**（报 `INJECT_EVENTS` 权限错误）。
需在手机「开发者选项 -> USB调试（安全设置）」打开开关（需登录小米账号）后，`adb shell input`
的 tap / swipe / keyevent 才能生效，回放才能驱动。

## 注意事项

- 银行 / 支付类 App 可能开启 `FLAG_SECURE`，uiautomator dump 会失败或截图黑屏，
  此时 bbox 自动跳过，坐标仍可用 getevent 拿到
- `getevent` 报权限错误时，坐标/bbox 自动降级为「询问或跳过」
- 验证码 / 刷脸 / 支付密码等环节需真人配合（需求文档已明确）
- 敏感信息（证件号、银行卡、密码、验证码）交付前需自行脱敏