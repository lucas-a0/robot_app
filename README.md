# xiaozhi-ble

小智语音客户端的 BLE 控制桥接模块，独立进程运行。

手机 App 通过 BLE GATT 实现"按住说话"（协议见 [BLE_CONTROL_PROTOCOL.md](BLE_CONTROL_PROTOCOL.md)），
本模块把 BLE 控制指令转发给对话模块（xiaozhi ROS2 节点）暴露的 Unix 控制 socket，
把对话模块推送的状态/错误/服务器地址以及本机的电池状态/网络状态/CPU/内存占用回传给 App，
并通过 ROS2 服务调用执行 App 下发的机器人行为命令（如站立、蹲下），
以及启停两个固定的导航 launch 任务（定位 bringup 与 Nav2 导航）、
向 `/xiaozhi_topic` 发布四个固定区域的导航目标、
通过 Unix socket 控制区域语音播放器列出/播放/停止提示音。

```text
手机 App  <-- BLE GATT -->  xiaozhi-ble（本模块）  <-- Unix socket -->  xiaozhi 对话模块
```

## 架构

- `gatt_server.py`：BlueZ GATT 外设（GLib 事件循环线程），实现 App 侧协议。
  客户端断开后会延迟几秒主动重新注册广播，兜底部分适配器断连后不再
  恢复广播、导致重新扫描不到设备的问题。该自愈依赖 App 已订阅 Command
  特征值的 Notify（BlueZ 在取消订阅或断连时调用 `StopNotify`）；未订阅
  则不会触发。刷新是盲式注销再注册，BlueZ 自己已恢复时也只会造成短暂
  广播空窗。
- `control_client.py`：Unix socket 客户端，自动重连，请求/响应匹配。
- `battery.py`：电池状态获取，在共享 rclpy 节点上订阅
  `sensor_msgs/BatteryState` 话题（默认 `/battery_state`），上报电量百分比和
  充电状态；ROS2 环境不可用时自动禁用，不影响其余功能。
- `ros_runtime.py`：共享 rclpy 运行时，持有进程级唯一的 rclpy 上下文、
  节点（`xiaozhi_ble`）和 spin 线程；battery、robot_control、zone_nav、
  cmd_vel 和 initial_pose 都挂载在这个节点上，不各自创建上下文。
- `robot_control.py`：机器人行为控制，把 App 写入的命令名按配置映射为
  `std_srvs/Trigger` 服务并异步调用；成功静默，失败通过特征值 Notify 上报。
- `network.py`：网络状态获取，直接从内核读取 WiFi 状态（SSID 用 wireless
  extensions ioctl，信号强度读 `/proc/net/wireless`，IPv4 地址用 netifaces 库），
  仅在状态变化时通知；没有无线网卡时自动禁用。
- `cpu.py`：CPU 使用率获取，周期读取 `/proc/stat` 累计 tick 并计算差分，
  变化超过阈值才通知；启动后首个读数约在一个轮询周期后产生。
- `memory.py`：内存占用获取，周期读取 `/proc/meminfo` 的 MemTotal /
  MemAvailable，上报已用/总量（MB）和占用率；变化超过阈值才通知。
- `bandwidth.py`：WiFi 带宽速率获取，周期读取 `/proc/net/dev` 的累计字节
  计数并计算差分（rx/tx 各多少 KB/s），变化超过阈值才通知；没有无线网卡时
  自动禁用。
- `latency.py`：对话服务器延迟探测，按周期向 WebSocket 地址的主机发起
  TCP 建连，以建连耗时近似 ping 延迟（无需 root 的 ICMP raw socket）；
  服务器地址由控制 socket 同步推送，地址不可用时探测自动挂起。
- `wifi_config.py`：WiFi 配网，把 App 写入的 SSID/密码通过 NetworkManager
  D-Bus 接口创建并激活连接（固定连接名 `xiaozhi-ble`），结果异步回调上报；
  NetworkManager 或无线网卡不可用时自动降级。
- `nav_tasks.py`：导航任务控制，把 App 写入的 START/STOP/STATUS 命令映射为
  两个固定的 `ros2 launch` 任务（`localization` 定位 bringup、`navigation`
  Nav2 导航），以独立进程组运行受管子进程，输出落日志文件，停止走进程组
  SIGINT（超时 SIGKILL），崩溃异步上报。
- `zone_nav.py`：区域导航，把 App 写入的四个固定区域名（`charging_zone`、
  `mowing_zone`、`pool_zone`、`equipment_zone`）作为 `std_msgs/String` 发布到
  `/xiaozhi_topic`（共享 rclpy 节点），发布即发即弃，即时应答 `OK <zone>`。
- `cmd_vel.py`：速度控制，把 App 写入的 `<linear_x> <angular_z>`（线速度
  m/s、角速度 rad/s）作为 `geometry_msgs/Twist` 发布到 `/cmd_vel`
  （共享 rclpy 节点）；每次写入发布一条消息，相同值不去重。
- `initial_pose.py`：初始位姿设置（重新定位），App 写入任意非空内容即触发，
  模块在共享 rclpy 节点上向 `/initialpose` 发布一条参数写死的
  `geometry_msgs/PoseWithCovarianceStamped`（`map` 坐标系，位姿与协方差
  硬编码在本模块内，App 不传坐标）；发布即发即弃，即时应答 `OK`。
- `zone_voice.py`：区域语音播放，把 App 写入的 LIST/PLAY/STOP/STATUS 翻译为
  zone_voice_player 的 JSON Lines Unix socket 请求；按周期查询播放状态，
  仅变化时 Notify（含机器人自行触发的播放与自然结束）。播放器未运行时
  自动降级。
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

电池状态是本模块自己的功能，不经过对话模块。模块在共享 rclpy 节点（见
`ros_runtime.py`）上订阅 `battery.topic` 配置的 `sensor_msgs/BatteryState` 话题
（默认 `/battery_state`，QoS 用 sensor data/best-effort，兼容可靠与尽力发布的
驱动），收到消息即更新缓存值。
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

### 内存占用

内存占用同样是本模块自己的功能。模块按 `memory.poll_interval_secs`（默认 5 秒）
周期读取 `/proc/meminfo` 的 `MemTotal` / `MemAvailable`（与 `free` 同源，无子进程
调用），上报已用内存、总内存（单位 MB）和占用率。占用率有瞬时值，首次采样即可
产生读数；仅当占用率变化达到 `memory.notify_threshold`（默认 1 个百分点）时才
通知 App，避免抖动导致频繁推送。`/proc/meminfo` 不可读时自动禁用，App 侧显示
`UNKNOWN`。

配置示例：

```yaml
memory:
  poll_interval_secs: 5
  notify_threshold: 1.0
```

### WiFi 带宽速率

带宽速率同样是本模块自己的功能。模块按 `bandwidth.poll_interval_secs`
（默认 5 秒）周期读取 `/proc/net/dev` 中网卡的累计收发字节，计算相邻两次
采样的差分得到上下行速率（KB/s，无子进程调用）。监控的网卡与网络状态
一致（`network.interface`，留空自动探测）；没有无线网卡时自动禁用，App 侧
显示 `UNKNOWN`。首次采样只建立基线；仅当 rx 或 tx 变化达到
`bandwidth.notify_threshold`（默认 10 KB/s）时才通知 App。

配置示例：

```yaml
bandwidth:
  poll_interval_secs: 5
  notify_threshold: 10.0
```

### 对话服务器延迟

延迟探测同样是本模块自己的功能。模块从控制 socket 同步当前生效的
WebSocket 地址，按 `latency.poll_interval_secs`（默认 5 秒）周期向其主机
发起 TCP 建连，以建连耗时近似 ping 延迟（不需要 root 的 ICMP raw socket，
无子进程调用）。尚未同步到地址时探测挂起，App 侧显示 `UNKNOWN`；服务器
不可达时上报 `LATENCY -`。仅当延迟变化达到
`latency.notify_threshold_ms`（默认 10 毫秒）或可达性变化时才通知 App。

配置示例：

```yaml
latency:
  poll_interval_secs: 5
  notify_threshold_ms: 10.0
  connect_timeout_secs: 2.0
```

### WiFi 配网

WiFi 配网同样是本模块自己的功能。App 向 WiFi Config 特征值写入
`<ssid>\n<password>` 两行文本（第二行为空或省略表示开放网络），模块通过
NetworkManager D-Bus 接口（dasbus，无子进程调用）创建并激活 WiFi 连接：
先删除同名的旧配置（固定连接名 `xiaozhi-ble`，保证密码更新生效），再
`AddAndActivateConnection` 并轮询设备状态直到激活、失败或超时。受理后
立即应答 `CONNECTING <ssid>`，最终结果（`CONNECTED <ssid>` /
`FAILED <code> <ssid>`，`code` 为 `auth`/`not_found`/`timeout`/`failed`）
通过特征值 Notify 异步上报；同时只允许一次配网，进行中写入收到 `ERR busy`。
需要系统运行 NetworkManager 且存在无线网卡，否则写入收到 `ERR unavailable`。

配置示例：

```yaml
wifi:
  connect_timeout_secs: 30.0   # 单次配网最长等待时间，超时按 FAILED timeout 上报
```

行为控制同样是本模块自己的功能。App 向 Robot Control 特征值写入命令名（如
`stand_up`），模块按 `robot_control.commands` 配置的“命令名 → ROS2 服务”映射，
在共享 rclpy 节点上异步调用对应的 `std_srvs/Trigger` 服务（服务类型固定为
Trigger，映射只需写服务名）。命令执行成功完全静默；失败（命令不存在、服务
不在线、`success=false`、调用超时）通过特征值 Notify 上报 `ERR ...`。
需要 ROS2 环境（同电池状态）；`commands` 留空则禁用，写入任何命令都会收到
`ERR unavailable`。

配置示例：

```yaml
robot_control:
  call_timeout_secs: 10.0    # 单次服务调用超时，超时按失败上报
  commands:
    stand_up: /base_bridge/stand_up
    lie_down: /base_bridge/lie_down
```

### 导航任务控制

导航任务控制同样是本模块自己的功能。App 向 Nav Task 特征值写入
`START <task> [key=value ...]` / `STOP <task>` / `STATUS`，模块把两个固定任务
（`localization` 定位 bringup、`navigation` Nav2 导航）作为受管子进程启停：
bash 包装脚本 source ROS2 与工作区环境后 `exec ros2 launch`（launch 参数走
`"$@"` argv，不进命令字符串），进程独立进程组，停止时先发进程组 SIGINT、
超时（10 秒）后 SIGKILL。任务参数白名单与默认值硬编码在 `nav_tasks.py`
（本功能不走 config.yaml）；`START navigation` 要求 `localization` 已在运行。
进程输出合并写入 `~/.cache/xiaozhi-ble/logs/<task>.log`（每次启动截断重写），
不通过 BLE 转发；进程自行退出（崩溃）时通过 Notify 上报
`EXITED <task> <code>` 并附最后一行输出摘要。

注意：桥接正常退出会停止仍在运行的任务；桥接崩溃可能留下孤儿的 launch
进程，需要人工上机清理后再 START 同名任务。导航工作区目录
（`~/mid360_nav_project/ros2_ws`）不存在时 START 返回 `ERR unavailable`。

### 区域导航

区域导航同样是本模块自己的功能。App 向 Zone Nav 特征值写入四个固定区域名
之一（`charging_zone`、`mowing_zone`、`pool_zone`、`equipment_zone`），模块在
共享 rclpy 节点上向 `zone_nav.topic` 配置的话题（默认 `/xiaozhi_topic`）
发布一条 `std_msgs/String` 消息（`data` 为小写区域名），后续导航行为由订阅
该话题的模块完成。发布即发即弃，写入的即时应答即最终结果：成功 `OK <zone>`，
区域名非法 `ERR command`，ROS 环境不可用时 `ERR unavailable`。`zone_nav.topic`
留空则禁用该功能。

配置示例：

```yaml
zone_nav:
  topic: /xiaozhi_topic     # 区域导航目标话题（std_msgs/String）
```

### 速度控制

速度控制也是本模块自己的功能。App 向 Cmd Vel 特征值写入
`<linear_x> <angular_z>`（线速度 m/s、角速度 rad/s，以空白分隔的两个
十进制数，例如 `-0.30 0.0`），模块在共享 rclpy 节点上向 `cmd_vel.topic`
配置的话题（默认 `/cmd_vel`）发布一条 `geometry_msgs/Twist` 消息
（`linear.x` / `angular.z`），后续运动行为由订阅该话题的模块完成。
每次写入发布一条消息，相同值不去重，面向摇杆式连续控制；数值范围与限幅
由运动模块负责，本模块只拒绝非数字、NaN/Inf 和字段个数不对的写入。
发布即发即弃，写入的即时应答即最终结果：成功 `OK <linear_x> <angular_z>`，
格式非法 `ERR command`，ROS 环境不可用时 `ERR unavailable`。`cmd_vel.topic`
留空则禁用该功能。

配置示例：

```yaml
cmd_vel:
  topic: /cmd_vel           # 速度控制话题（geometry_msgs/Twist）
```

### 区域语音播放

区域语音播放同样是本模块自己的功能。App 向 Zone Voice 特征值写入
`LIST` / `PLAY <file>` / `STOP` / `STATUS`（纯文本，文件名与 `LIST` 返回的
完全一致），模块作为 JSON Lines 客户端连接 `zone_voice_player` 暴露的 Unix
socket（默认 `/tmp/zone_voice_player.sock`），把请求转过去并把应答映射为
`LIST ...` / `PLAYING <file>` / `IDLE` / `ERR ...`。播放状态按
`zone_voice.poll_interval_secs`（默认 1 秒）轮询，仅变化时 Notify，因此
机器人自行触发的播放和音频自然结束 App 也能看到。`socket_path` 留空则禁用；
播放器未运行时 `LIST`/`PLAY`/`STOP` 回复 `ERR unavailable`，状态为 `UNKNOWN`。

配置示例：

```yaml
zone_voice:
  socket_path: /tmp/zone_voice_player.sock
  request_timeout_secs: 2.0
  poll_interval_secs: 1.0
```

### 初始位姿设置

App 向 Initial Pose 特征值写入任意非空内容（如 `1`），即可让机器人重新设置
初始位姿。写入内容本身没有含义，只是“现在重新校准”的触发信号；位姿参数由
机器人侧固定（`map` 坐标系，`x = 0.9512255787849426`、
`y = -0.6430897116661072`，朝向四元数 `z = 0.017019178285167157`、
`w = 0.9998551632964134`，协方差对角线 `x/y = 0.25`、
`yaw = 0.06853891945200942`），硬编码在 `initial_pose.py`，不走 config.yaml。

模块在共享 rclpy 节点上向 `initial_pose.topic` 配置的话题（默认
`/initialpose`）发布一条 `geometry_msgs/PoseWithCovarianceStamped` 消息
（`header.stamp` 为发布时刻，`frame_id` 为 `map`），后续重新定位行为由订阅
该话题的模块（如 AMCL）完成。每次写入发布一条消息，相同内容不去重；空写入
回复 `ERR command`，ROS 环境不可用时回复 `ERR unavailable`。`initial_pose.topic`
留空则禁用该功能。`OK` 只表示消息已交给 ROS2 发布者，不表示重定位已完成。

配置示例：

```yaml
initial_pose:
  topic: /initialpose       # 初始位姿话题（geometry_msgs/PoseWithCovarianceStamped）
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
