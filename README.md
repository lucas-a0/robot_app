# xiaozhi-ble

小智语音客户端的 BLE 控制桥接模块，独立进程运行。

手机 App 通过 BLE GATT 实现"按住说话"（协议见 [BLE_CONTROL_PROTOCOL.md](BLE_CONTROL_PROTOCOL.md)），
本模块把 BLE 控制指令转发给对话模块（xiaozhi ROS2 节点）暴露的 Unix 控制 socket，
并把对话模块推送的状态/错误/服务器地址以及本机的电池状态/网络状态回传给 App。

```text
手机 App  <-- BLE GATT -->  xiaozhi-ble（本模块）  <-- Unix socket -->  xiaozhi 对话模块
```

## 架构

- `gatt_server.py`：BlueZ GATT 外设（GLib 事件循环线程），实现 App 侧协议。
  客户端断开后会延迟几秒主动重新注册广播，兜底部分适配器断连后不再
  恢复广播、导致重新扫描不到设备的问题。
- `control_client.py`：Unix socket 客户端，自动重连，请求/响应匹配。
- `battery.py`：电池状态获取，内置 rclpy 节点订阅 `sensor_msgs/BatteryState`
  话题（默认 `/battery_state`），上报电量百分比和充电状态；ROS2 环境不可用时
  自动禁用，不影响其余功能。
- `network.py`：网络状态获取，直接从内核读取 WiFi 状态（SSID 用 wireless
  extensions ioctl，信号强度读 `/proc/net/wireless`，IPv4 地址用 netifaces 库），
  仅在状态变化时通知；没有无线网卡时自动禁用。
- `cpu.py`：CPU 使用率获取，周期读取 `/proc/stat` 累计 tick 并计算差分，
  变化超过阈值才通知；启动后首个读数约在一个轮询周期后产生。
- `main.py`：桥接装配（命令转发、通知分发、断连兜底）。
- `config.py`：YAML 配置加载。

### 控制 socket 协议（对话模块侧实现）

行文本协议。请求一行一条，响应以 `OK`/`ERR` 开头，其余为推送通知：

| 方向 | 内容 | 说明 |
|------|------|------|
| 请求 | `start` / `stop` | 开始/结束收音，响应 `OK start` / `OK stop` 或 `ERR <code> <msg>` |
| 请求 | `get_url` | 查询当前 WebSocket 地址，响应 `OK url <url>` |
| 请求 | `set_url <url>` | 修改 WebSocket 地址，响应 `OK url <url>` 或 `ERR CONFIG_* <msg>` |
| 推送 | `STATE <state>` | 状态变化；新连接立即收到快照 |
| 推送 | `URL <url>` | 地址变化 |
| 推送 | `ERROR <code> <msg>` / `NONE` | 错误上报/清除 |

socket 断连时本模块向 App 上报 `ERROR VOICE_UNAVAILABLE`，重连后由快照恢复显示。
`URL` 推送只在地址变化时发生，因此 socket 连接/重连后本模块会主动发送一次
`get_url`，把当前生效地址同步到 URL 特征值，保证 App 订阅后能立即看到地址。

### 电池状态

电池状态是本模块自己的功能，不经过对话模块。模块内嵌一个 rclpy 节点，订阅
`battery.topic` 配置的 `sensor_msgs/BatteryState` 话题（默认 `/battery_state`，
QoS 用 sensor data/best-effort，兼容可靠与尽力发布的驱动），收到消息即更新缓存值。
消息中 `percentage` 为 NaN（无读数）时保留上一次值；`power_supply_status` 映射为
`CHARGING` / `DISCHARGING` / `NOT_CHARGING` / `FULL` 上报给 App。`battery.topic`
留空则禁用，App 侧显示 `UNKNOWN`。GATT 特征值仍按 1 秒周期向 App 重发缓存值。

要求进程带 ROS2 环境（rclpy 与 sensor_msgs 可导入）；systemd 部署时见
`xiaozhi-ble.service`，`ExecStart` 会先 source `/opt/ros/humble/setup.bash`。
使用非默认 DDS 域时在 service 中加一行 `Environment=ROS_DOMAIN_ID=<id>`。

配置示例：

```yaml
battery:
  topic: /battery_state
```

### 网络状态

网络状态同样是本模块自己的功能。模块按 `network.poll_interval_secs`（默认 5 秒）
周期从内核读取 WiFi 状态：SSID 用 wireless extensions ioctl，信号强度读
`/proc/net/wireless`，IPv4 地址用 netifaces 库，全程无子进程调用；仅在状态变化
时通知 App。`network.interface` 留空时自动探测第一个无线网卡；没有无线网卡时
自动禁用，App 侧显示 `UNKNOWN`。

配置示例：

```yaml
network:
  interface: ""            # 留空自动探测，如 wlan0
  poll_interval_secs: 5
```

### CPU 使用率

CPU 使用率同样是本模块自己的功能。模块按 `cpu.poll_interval_secs`（默认 5 秒）
周期读取 `/proc/stat` 的累计 tick 计数，计算相邻两次采样的差分得到整机使用率
（与 `top` 同源，无子进程调用）。首次采样只建立基线，首个读数约在一个轮询周期
后产生；仅当使用率变化达到 `cpu.notify_threshold`（默认 1 个百分点）时才通知
App，避免抖动导致频繁推送。

配置示例：

```yaml
cpu:
  poll_interval_secs: 5
  notify_threshold: 1.0
```

## 依赖安装

`PyGObject`（`gi`）和 BlueZ 只能通过系统包安装，因此 venv 必须带
`--system-site-packages`，且使用系统 Python 3.10：

```bash
sudo apt install bluez python3-gi
cd xiaozhi-ble
uv venv --python /usr/bin/python3.10 --system-site-packages
uv pip install -e ".[dev]"
```

`netifaces` 由 uv 安装，但它是 C 扩展，从源码构建时需要编译环境
（`gcc` 与 `python3-dev`）；没有编译环境的机器可改用系统包
`sudo apt install python3-netifaces`（venv 已带 `--system-site-packages`，
可直接使用系统包）。

## 运行

对话模块需配置 `control.mode: external`（监听默认 `/tmp/xiaozhi-control.sock`）。

```bash
cp config.yaml.example ~/.config/xiaozhi-ble/config.yaml
# 按需修改 socket 路径、适配器、广播名称
uv run xiaozhi-ble
# 或指定配置文件
uv run xiaozhi-ble --config /path/to/config.yaml
```

## 部署（systemd）

模块自带 `xiaozhi-ble.service`。移动模块目录后，修改文件里标注的三处路径
（`WorkingDirectory`、`ExecStart`），然后：

```bash
sudo cp xiaozhi-ble.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xiaozhi-ble.service
```

与对话模块（`xiaozhi.service`）无启动顺序依赖：socket 客户端会自动重连，
两侧任意先后启动、任意一方重启都能自动恢复。

## 测试

```bash
uv run pytest tests/
```

GATT 测试不依赖真实蓝牙适配器（只构造 D-Bus 接口对象）；socket 客户端测试
使用本地假服务端。

注意：如果当前 shell 已 source 过 ROS2 环境（如 `source /opt/ros/humble/setup.bash`），
ROS 自带的 pytest 插件会被自动加载并与项目依赖的 pytest 版本冲突，导致测试无法
启动。此时换一个未 source 的 shell，或显式禁用插件自动加载：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/
```
