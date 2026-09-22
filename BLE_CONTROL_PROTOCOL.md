# 小智机器人 BLE 控制协议

本文档定义手机 App 与小智机器人之间的 BLE（Bluetooth Low Energy）控制协议。
局域网 TCP 兜底通道见 [LAN_CONTROL_PROTOCOL.md](LAN_CONTROL_PROTOCOL.md)。

协议版本：`2.1`（相对 2.0 为兼容扩展：Robot Control 增加前进 / 后退 / 转向 /
跳舞 / 点头等命令名。2.0 相对 1.12 为破坏性变更：删除按住说话 / WebSocket URL /
Error / 对话服务器延迟四个特征值；新增只读的 LAN Endpoint 与 Audio Endpoint）。

## 1. 适用范围

- 本协议定义手机 App 与机器人之间的 BLE 控制通道，由机器人侧实现。
  App 只与本协议交互，不感知机器人内部实现。
- BLE 只传输控制命令和机器人状态，不通过 BLE 传输音频。语音对话由 App 完成；
  App 通过 LAN Endpoint / Audio Endpoint 得知本机地址后，把 PCM 打到音频口。
- Android 15 起一条 BLE 连接大约只能订阅 15 路 Notify。本服务当前占用 12 路
  Notify；LAN Endpoint 与 Audio Endpoint **不加 Notify**。

## 2. GATT 定义

| 项目 | 值 |
|---|---|
| 广播名称默认值 | `Xiaozhi` |
| Service UUID | `12345678-1234-5678-1234-56789abcdef0` |

Service 内的特征值按功能分类，UUID 均为 `12345678-1234-5678-1234-56789abcdefN`
（`abcdefN` 尾号已用尽，其后的新特征值改用 `12345678-1234-5678-1234-56789abcdNNN`，
当前已用到 `abcd012`）：

| 类别 | 特征值 | UUID 尾号 | 属性 |
|---|---|---|---|
| 状态监测 | Battery Status Characteristic | `abcdef4` | Read、Notify |
| 状态监测 | Network Status Characteristic | `abcdef5` | Read、Notify |
| 状态监测 | CPU Status Characteristic | `abcdef6` | Read、Notify |
| 状态监测 | Bandwidth Status Characteristic | `abcdef7` | Read、Notify |
| 行为控制 | Robot Control Characteristic | `abcdef9` | Read、Write、Notify |
| 连接配置 | WiFi Config Characteristic | `abcdefa` | Read、Write、Notify |
| 任务控制 | Nav Task Characteristic | `abcdefb` | Read、Write、Notify |
| 任务控制 | Zone Nav Characteristic | `abcdefc` | Read、Write、Notify |
| 运动控制 | Cmd Vel Characteristic | `abcdefd` | Read、Write、Notify |
| 状态监测 | Memory Status Characteristic | `abcdefe` | Read、Notify |
| 音频控制 | Zone Voice Characteristic | `abcdeff` | Read、Write、Notify |
| 定位配置 | Initial Pose Characteristic | `abcd010` | Read、Write、Notify |
| 连接发现 | LAN Endpoint Characteristic | `abcd011` | Read |
| 连接发现 | Audio Endpoint Characteristic | `abcd012` | Read |

已删除、不再出现在 Service 中的 UUID：`abcdef1`（Command）、`abcdef2`（URL）、
`abcdef3`（Error）、`abcdef8`（Latency）。保留特征值的 UUID 不变。

广播名称可在机器人侧配置修改。App 应以 Service UUID 识别设备，不应只依赖广播名称。

## 3. 数据格式

- App 写入和机器人通知均为 UTF-8 文本。
- 每条消息只包含一条完整指令或响应，不添加 JSON 包装。
- App 写入时可以带首尾空白；机器人解析时会去除首尾空白并忽略命令大小写。
- 特征值按 180 字节上限处理，超长文本由机器人侧截断。
- App 应优先使用 GATT Write With Response，确认写入已被 BLE 协议栈接收；
  需要高频连续写入的 Cmd Vel 特征值除外（见第 11 节）。
- 状态监测类特征值在数据尚未获取到时统一报 `UNKNOWN`；字段级缺失用占位符（如 `-`）。
- 各特征值的具体文本格式见对应章节：LAN Endpoint 见第 4 节，Audio Endpoint 见第 5 节，
  状态监测各特征值见第 6 节，Robot Control 见第 7 节，WiFi Config 见第 8 节，
  Nav Task 见第 9 节，Zone Nav 见第 10 节，Cmd Vel 见第 11 节，
  Zone Voice 见第 12 节，Initial Pose 见第 13 节。

## 4. 局域网控制口（LAN Endpoint）

App 读取 LAN Endpoint 特征值，获得机器人当前的局域网控制地址，用于建立
[LAN_CONTROL_PROTOCOL.md](LAN_CONTROL_PROTOCOL.md) 中的 TCP 控制连接（蓝牙断开后的兜底）。

- 只读，无 Notify。端口几乎不变；IP 变化已经由 Network Status 推送。
- 值为 UTF-8 文本 `<ipv4> <port>`，例如 `192.168.1.12 4205`。
- 尚无 IPv4（未连上 WiFi）时整个值为 `UNKNOWN`。
- App 在 BLE 连上后 Read 一次即可；TCP 连不上或 WiFi 重连后应再 Read。

## 5. 音频下发口（Audio Endpoint）

App 读取 Audio Endpoint 特征值，获得把播放用 PCM 打到哪台机器的哪个端口。
语音对话由 App 完成，机器人只负责按该地址接收裸 PCM 并播放。

- 只读，无 Notify。
- 值为 UTF-8 文本 `<ipv4> <port>`，例如 `192.168.1.12 4203`。
- 尚无 IPv4，或本机 PCM 播放服务查询失败时整个值为 `UNKNOWN`。不要把 PCM
  打向 `UNKNOWN`。
- PCM 格式为 16-bit 小端、单声道、24000 Hz，走独立 TCP 连接，不经过本特征值、
  也不经过 LAN 控制口。

## 6. 状态监测

状态监测类特征值均为只读（Read、Notify），App 不应写入；订阅成功后立即收到当前
缓存值，之后按各自的通知策略推送。电池、CPU 和内存属于机身状态，WiFi 连接和带宽
属于网络状态。

### 6.1 电池状态（Battery Status）

App 可以读取或订阅 Battery Status 特征值来获取机器人的电池状态。

- 通知周期为 1 秒（重发最近一次获取到的缓存值）。
- 值为 UTF-8 文本，格式为 `<percentage> <supply_status>`，两个字段以单个空格分隔：
  - `percentage` 为电量百分比的小数形式（0 到 1），例如 `0.670` 表示电量 67%；
    尚未收到有效读数时为 `UNKNOWN`。
  - `supply_status` 为供电状态：`CHARGING`（充电中）、`DISCHARGING`（放电中）、
    `NOT_CHARGING`（未在充电）、`FULL`（已充满）；尚未收到有效读数时为 `UNKNOWN`。
- 两个字段都尚未获取到时，整个值为 `UNKNOWN`（例如机器人未配置电池信息源）。

示例：`0.670 CHARGING`、`0.982 FULL`、`UNKNOWN DISCHARGING`、`UNKNOWN`。

### 6.2 CPU 使用率（CPU Status）

App 可以读取或订阅 CPU Status 特征值来获取机器人的整机 CPU 使用率。

- 使用率没有瞬时值，机器人启动后首个读数需要约一个采样周期才能产生；此前值为 `UNKNOWN`。
- 仅在使用率变化达到阈值（默认 1 个百分点）时通知，避免 CPU 抖动导致频繁推送。
- 值为 UTF-8 文本，格式为 `CPU <usage>`，`usage` 为 0-100 的百分比、保留一位
  小数，例如 `CPU 23.5`；尚未获取到读数时为 `UNKNOWN`。

### 6.3 网络状态

网络状态由两个特征值组成：WiFi 连接状态（连没连、信号、IP、SSID）和 WiFi 带宽速率
（实时上行/下行吞吐）。

#### 6.3.1 WiFi 连接状态（Network Status）

App 可以读取或订阅 Network Status 特征值来获取机器人的 WiFi 连接状态。

- 仅在状态变化时通知。
- 值为 UTF-8 文本，可能取值：

| 值 | 含义 |
|---|---|
| `UNKNOWN` | 尚未获取到网络状态（机器人刚启动或没有无线网卡） |
| `DISCONNECTED` | WiFi 未连接 |
| `WIFI <rssi> <ip> <ssid>` | WiFi 已连接；`rssi` 为信号强度（整数，单位 dBm），`ip` 为 IPv4 地址，`ssid` 为剩余全部文本 |

`ssid` 可能包含空格，App 解析时应先取前三个字段（`WIFI`、`rssi`、`ip`），其余部分
作为 SSID。`rssi` 或 `ip` 为 `-` 时表示该字段未知。

示例：`WIFI -38 172.16.0.195 MyHome`、`WIFI -52 - Office AP 2F`。

#### 6.3.2 WiFi 带宽速率（Bandwidth Status）

App 可以读取或订阅 Bandwidth Status 特征值来获取机器人 WiFi 网卡的实时吞吐速率。

- 速率没有瞬时值，机器人启动后首个读数需要约一个采样周期才能产生；此前值为 `UNKNOWN`。
- 仅在 rx 或 tx 变化达到阈值（默认 10 KB/s）时通知。
- 值为 UTF-8 文本，格式为 `BANDWIDTH <rx_kbps> <tx_kbps>`：
  - `rx_kbps` 为下行速率，单位 KB/s，保留一位小数；
  - `tx_kbps` 为上行速率，单位 KB/s，保留一位小数。
- 尚未获取到读数（机器人刚启动或没有无线网卡）时整个值为 `UNKNOWN`。

示例：`BANDWIDTH 1234.5 56.7`、`UNKNOWN`。

### 6.4 内存占用（Memory Status）

App 可以读取或订阅 Memory Status 特征值来获取机器人的整机内存占用。

- 内存占用有瞬时值，订阅后即可收到当前读数（尚未完成首次采样时为 `UNKNOWN`）。
- 仅在占用率变化达到阈值（默认 1 个百分点）时通知，避免抖动导致频繁推送。
- 值为 UTF-8 文本，格式为 `MEM <used_mb> <total_mb> <percent>`，三个字段以单个
  空格分隔：
  - `used_mb` 为已用内存，整数，单位 MB；
  - `total_mb` 为总内存，整数，单位 MB；
  - `percent` 为占用率，0-100 的百分比、保留一位小数。
- 尚未获取到读数时整个值为 `UNKNOWN`。

示例：`MEM 2145 7872 27.2`、`UNKNOWN`。

## 7. 机器人行为控制（Robot Control）

App 可以向 Robot Control 特征值写入命令名，控制机器人执行预配置的行为（站立、
蹲下、前进、后退、转向、跳舞、点头）。写入的命令名查表后异步执行。

- App 写入内容为命令名（UTF-8 文本）；机器人解析时去除首尾空白并忽略大小写。
- 固定命令名：

| 命令名 | 含义 |
|---|---|
| `go_forward` | 前进一小段后自动停下 |
| `go_back` | 后退一小段后自动停下 |
| `turn_left` | 左转一小段后自动停下 |
| `turn_right` | 右转一小段后自动停下 |
| `dance` | 跳舞（左右小幅转向） |
| `nod` | 点头（短距前后点两次） |
| `stand_up` | 站起 |
| `squat` / `lie_down` | 蹲下（两者等价） |

- 命令执行**成功时完全静默**，不发送任何通知。
- 仅在失败时通过 Notify 上报错误，格式为 `ERR ...`；读取该特征值返回最近一次
  上报的错误（尚无错误时为空）：

| 通知内容 | 含义 |
|---|---|
| `ERR encoding` | 写入内容不是合法 UTF-8 |
| `ERR command` | 命令名未在机器人侧映射表中配置 |
| `ERR unavailable` | 机器人控制功能不可用 |
| `ERR unavailable <command>` | 命令对应的行为当前不可用 |
| `ERR failed <command> <message>` | 服务返回 `success=false`（`message` 为服务给出的原因）或调用发生异常 |
| `ERR timeout <command>` | 服务调用超过机器人侧配置的超时时间（默认 10 秒） |
| `ERR internal` | 机器人内部未能调度命令 |

行为类命令通常耗时数秒，错误上报是异步的：App 应先订阅该特征值的 Notify 再写入
命令，否则可能收不到错误通知。`ERR failed`/`ERR timeout` 中的 `<command>` 为机器人
实际执行的命令名（已规范化），便于 App 区分并发命令的结果。

## 8. WiFi 配网（WiFi Config）

App 可以向 WiFi Config 特征值写入目标 WiFi 的 SSID 和密码，机器人连接指定
WiFi，并把连接结果通过 Notify 异步上报。机器人侧保存该 WiFi 配置，重启网络后
自动重连。

### 8.1 写入格式（App → 机器人）

写入内容为 UTF-8 文本两行，以单个换行符 `\n` 分隔：

```text
<ssid>\n<password>
```

- 第一行为 SSID，整行即 SSID（SSID 可能包含空格）；不能为空，按 IEEE 802.11 上限
  不超过 32 字节。
- 第二行为密码；空行或省略第二行均表示连接开放（无密码）网络。WPA/WPA2 密码为
  8-63 个字符。
- 尾部换行可选；机器人解析时会去除各行首尾空白。
- 写入总长不超过 180 字节；App 应在写入前协商至少 183 字节的 ATT MTU。

### 8.2 应答与结果（机器人 → App）

App 必须先订阅 WiFi Config 特征值的 Notify 再写入，否则可能收不到结果通知。连接
通常需要数秒到数十秒，应答与结果分两条通知：

| 通知内容 | 含义 |
|---|---|
| `CONNECTING <ssid>` | 写入校验通过，机器人已开始连接（**即时应答，不代表连接成功**） |
| `CONNECTED <ssid>` | 连接成功（已关联 AP 并获取到 IP 地址） |
| `FAILED auth <ssid>` | 密码错误或鉴权失败 |
| `FAILED not_found <ssid>` | 找不到该 AP（SSID 不在覆盖范围或拼写错误） |
| `FAILED timeout <ssid>` | 超过机器人侧配置的超时时间（默认 30 秒）仍未完成连接 |
| `FAILED failed <ssid>` | 其他原因导致的连接失败 |

写入不合法或机器人当前无法受理时，即时回复错误（不会有后续 `CONNECTED`/`FAILED`）：

| 通知内容 | 含义 |
|---|---|
| `ERR encoding` | 写入内容不是合法 UTF-8 |
| `ERR invalid` | 格式不符（SSID 为空或超长、密码长度非法） |
| `ERR busy` | 上一次配网仍在进行中；机器人同时只处理一次配网 |
| `ERR unavailable` | 配网功能不可用 |

`ssid` 一律为通知的最后一个字段，可能包含空格；App 解析时先取前面的固定字段，
其余部分作为 SSID。读取该特征值返回最近一次通知文本（尚无通知时为空）。

连接过程中机器人的 Network Status 特征值会先变为 `DISCONNECTED`、成功后再变为
`WIFI ...`（新 SSID）；App 判断本次配网成败应以 WiFi Config 的结果通知为准。

### 8.3 标准交互时序

```text
App                                      Robot
 |---- Subscribe WiFi Config Notify ---->|
 |---- Write "MyHome\n12345678" -------->|
 |<------------------ CONNECTING MyHome --|
 |              （机器人切换 WiFi 中）     |
 |<------------------- CONNECTED MyHome --|
 |<--- Network Status: WIFI -42 ... MyHome|  由状态监测特征值另行上报
```

## 9. 导航任务控制（Nav Task）

App 可以向 Nav Task 特征值写入命令，在机器人上启动或停止两个固定的导航
任务：`localization`（定位）和 `navigation`（导航）。两个任务的默认参数
由机器人侧固定（见 9.2），App 只能在白名单内覆盖参数。

- 机器人侧服务正常停止（含服务重启）时会先停止仍在运行的任务。
- 任务输出不通过 BLE 转发。
- 写入/通知均为 UTF-8 文本，通知不超过 180 字节；带参数覆盖的写入可能较长，写入前
  建议协商至少 183 字节的 ATT MTU。机器人解析时去除首尾空白，
  命令动词与任务名忽略大小写；参数 key 小写规范化，value 原样保留。

### 9.1 写入命令（App → 机器人）

| 写入内容 | 含义 |
|---|---|
| `START <task>` | 以默认参数启动任务 |
| `START <task> key=value ...` | 覆盖部分参数后启动任务（未覆盖的 key 用默认值） |
| `STOP <task>` | 停止任务 |
| `STATUS` | 查询两个任务的当前状态 |

- `<task>` 为 `localization` 或 `navigation`。
- `key=value` 之间以空白分隔；key 必须在 9.2 的白名单内；value 不能为空、不能含
  空白字符。
- `START navigation` 要求 `localization` 处于 `RUNNING`，否则拒绝（见
  `ERR state localization not_running`）；机器人不会自动代起 localization，
  启动顺序由 App 控制。

### 9.2 可覆盖参数与默认值

`localization`：

| key | 默认值 |
|---|---|
| `map` | `/home/sunrise/mid360_nav_project/ros2_ws/src/robot_nav617/navigation/maps/fastlio_map_nav2_v1/map.yaml` |
| `params_file` | `/home/sunrise/mid360_nav_project/ros2_ws/src/robot_nav617/navigation/config/nav2_mid360_params_exhibition.yaml` |
| `scan_topic` | `/scan` |
| `start_lidar` | `true` |
| `start_lio` | `true` |
| `start_scan` | `true` |
| `start_localization` | `true` |

`navigation`：

| key | 默认值 |
|---|---|
| `params_file` | `/home/sunrise/mid360_nav_project/ros2_ws/src/robot_nav617/navigation/config/nav2_mid360_params_exhibition.yaml` |
| `start_lio_odom_twist` | `true` |
| `start_xiaozhi_bridge` | `false` |
| `use_sim_time` | `false` |
| `autostart` | `true` |

默认值可能随机器人部署调整，App 不应把默认值写死在本地；需要默认值时省略该 key
即可。

### 9.3 应答与状态通知（机器人 → App）

App 必须先订阅 Nav Task 特征值的 Notify 再写入；订阅成功后机器人立即推送当前
`STATE ...`。`STARTED`/`STOPPING`/`ERR` 为写入的即时应答，`STOPPED`/`EXITED`
为异步通知：

| 通知内容 | 含义 |
|---|---|
| `STARTED <task>` | 即时应答：launch 进程已拉起（**不代表**内部节点已就绪） |
| `STOPPING <task>` | 即时应答：停止请求已接受，正在优雅关停 |
| `STOPPED <task>` | 异步：任务进程已退出（由 `STOP` 触发） |
| `EXITED <task> <code> [detail]` | 异步：任务进程自行退出（崩溃或被外部杀死）；`code` 为退出码；`detail` 为最后一行非空输出摘要，可能缺省或被截断 |
| `STATE localization <s1> navigation <s2>` | `STATUS` 的应答及订阅后的初始推送；`<sN>` 为 `RUNNING`/`STOPPING`/`STOPPED` |

错误应答（写入的即时回复，无后续通知）：

| 通知内容 | 含义 |
|---|---|
| `ERR encoding` | 写入内容不是合法 UTF-8 |
| `ERR command` | 无法识别的命令（动词不是 START/STOP/STATUS，或格式不符） |
| `ERR task <task>` | 任务名不存在 |
| `ERR param <task> <key>` | 参数 key 不在白名单，或 value 为空/含空白字符 |
| `ERR state <task> already_running` | 任务已在运行，重复 START |
| `ERR state <task> stopping` | 任务正在停止中，稍后再试 |
| `ERR state <task> not_running` | 任务未在运行，无法 STOP |
| `ERR state localization not_running` | START navigation 时 localization 未在运行 |
| `ERR unavailable` | 导航工作区不存在，功能不可用 |
| `ERR spawn <task> <message>` | 进程拉起失败 |
| `ERR internal` | 机器人内部未能调度命令 |

读取该特征值返回最近一次通知文本（尚无通知时为当前 `STATE ...`）。

`STARTED` 只表示任务已启动；任务内部就绪需要数秒到数十秒，App 不应依据
`STARTED` 立即假定可以下发导航目标。

### 9.4 标准交互时序

```text
App                                      Robot
 |---- Subscribe Nav Task Notify ------->|
 |<- STATE localization STOPPED navigation STOPPED
 |---- Write "START localization" ------>|
 |<---------------- STARTED localization -|
 |             （launch 进程运行中）       |
 |---- Write "START navigation" -------->|
 |<----------------- STARTED navigation --|
 |                                         |
 |---- Write "STOP navigation" --------->|
 |<----------------- STOPPING navigation -|
 |<------------------ STOPPED navigation -|
```

异常示例：任务异常退出时机器人主动推送，如
`EXITED navigation 1 [amcl]: map could not be loaded`。

## 10. 区域导航（Zone Nav）

App 可以向 Zone Nav 特征值写入区域名，让机器人导航到四个固定区域之一：
`charging_zone`（充电区）、`mowing_zone`（割草区）、`pool_zone`（泳池区）、
`equipment_zone`（设备区）。机器人侧向 ROS2 话题 `/xiaozhi_topic` 发布一条
`std_msgs/String` 消息（`data` 为区域名），后续的导航行为由订阅该话题的模块
完成。

- 写入内容为区域名（UTF-8 文本），只有上述四个合法值；机器人解析时去除首尾
  空白并忽略大小写，发布到话题的 `data` 一律为小写区域名。
- 发布是即发即弃（fire-and-forget），写入的即时应答即最终结果：

| 通知内容 | 含义 |
|---|---|
| `OK <zone>` | 消息已发布到 `/xiaozhi_topic`（`<zone>` 为小写区域名） |
| `ERR encoding` | 写入内容不是合法 UTF-8 |
| `ERR command` | 区域名不是四个合法值之一（含空写入） |
| `ERR unavailable` | 区域导航功能不可用 |
| `ERR internal` | 机器人内部未能调度命令 |

读取该特征值返回最近一次应答文本（尚无应答时为空）。`OK` 只表示消息已交给
ROS2 发布者，不表示机器人已开始移动或已到达目标区域；导航进度与到达情况
不通过 BLE 上报。

## 11. 速度控制（Cmd Vel）

App 可以向 Cmd Vel 特征值写入线速度和角速度，直接控制机器人运动。机器人侧
每收到一次写入，就向 ROS2 话题 `/cmd_vel` 发布一条 `geometry_msgs/Twist`
消息（`linear.x` 为线速度、`angular.z` 为角速度），后续运动行为由订阅该话题
的模块（如底盘驱动）完成。

- 写入内容为两个十进制数，以空白分隔：`<linear_x> <angular_z>`
  （线速度 m/s、角速度 rad/s），例如 `-0.30 0.0`；机器人解析时去除首尾空白。
- **每次写入发布一条消息**：即使写入的值与上次完全相同也会再次发布，机器人
  侧不做去重。该特征值面向摇杆式连续控制，App 可按需连续写入；BLE 连接间隔
  最小 7.5 ms，理论上限约每秒百次量级，实际建议不超过 20-30 Hz。
- 数值范围与限幅由机器人运动模块负责，本特征值不限制大小；非数字、NaN/Inf、
  字段个数不对的写入按 `ERR command` 拒绝。
- 机器人对 `/cmd_vel` 有超时保护：App 停止写入后，超过机器人侧的消息超时
  时间会自动停车，BLE 断连、App 崩溃等情况同理。超时时间由机器人侧配置
  决定，不属于本协议范围；App 需要立即停车时仍可显式写入一次 `0 0`，
  不必等待超时生效。
- 发布是即发即弃（fire-and-forget），写入的即时应答即最终结果：

| 通知内容 | 含义 |
|---|---|
| `OK <linear_x> <angular_z>` | 消息已发布到 `/cmd_vel` |
| `ERR encoding` | 写入内容不是合法 UTF-8 |
| `ERR command` | 格式不符或数值非法（含空写入） |
| `ERR unavailable` | 速度控制功能不可用 |
| `ERR internal` | 机器人内部未能调度命令 |

读取该特征值返回最近一次应答文本（尚无应答时为空）。`OK` 只表示消息已交给
ROS2 发布者，不表示机器人已开始运动。连续写入时建议使用 Write Without Response
并控制写入节奏（见上文 20-30 Hz 的建议），避免超出连接间隔承载能力。

## 12. 区域语音播放（Zone Voice）

App 可以向 Zone Voice 特征值写入命令，列出、播放或停止机器人上的区域提示音，
并查询当前播放状态。可播文件由机器人侧维护，App 应通过 `LIST` 获取当前列表，
不要把文件名写死在本地。

- 写入/通知均为 UTF-8 文本，通知不超过 180 字节。
- 机器人解析时去除首尾空白；命令动词忽略大小写。
- **音频文件名大小写敏感、原样保留**（必须与 `LIST` 返回的名字完全一致）。
- 音频文件名不含空白字符；`LIST` 以空格分隔文件名。`PLAY` 取动词后的剩余文本
  作为文件名。
- `PLAY` 会打断当前正在播放的音频（无论该播放是 App 触发还是机器人自行触发）。
- `STOP` 可停止当前播放，包括机器人自行触发的播放。
- 音频自然播完后状态自动回到 `IDLE`；机器人自行开始播放时也会通知 `PLAYING`。
  App 订阅后即可通过 Notify 跟踪状态，不必轮询 `STATUS`。

### 12.1 写入命令（App → 机器人）

| 写入内容 | 含义 |
|---|---|
| `LIST` | 列出当前可播放的音频文件 |
| `PLAY <audio>` | 播放指定音频（打断当前播放） |
| `STOP` | 停止当前播放 |
| `STATUS` | 查询当前播放状态 |

### 12.2 应答与状态通知（机器人 → App）

App 必须先订阅 Zone Voice 特征值的 Notify 再写入；订阅成功后机器人立即推送
当前状态（`PLAYING ...` / `IDLE` / `UNKNOWN`）。`LIST`/`PLAY`/`STOP`/`STATUS`
的应答均为写入的即时回复；播放开始或结束后的状态变化由机器人主动推送：

| 通知内容 | 含义 |
|---|---|
| `LIST <file> <file> ...` | `LIST` 应答；文件名按名称排序、含扩展名；没有文件时为单独的 `LIST` |
| `PLAYING <file>` | 正在播放；来自 `PLAY` 成功、`STATUS`、订阅快照或状态变化 |
| `IDLE` | 空闲（已停止或自然播完） |
| `UNKNOWN` | 尚未获取到播放状态（播放功能不可用） |

错误应答（写入的即时回复）：

| 通知内容 | 含义 |
|---|---|
| `ERR encoding` | 写入内容不是合法 UTF-8 |
| `ERR command` | 无法识别的命令，或 `PLAY` 缺少文件名 |
| `ERR audio <file>` | 指定音频不在当前可播列表中 |
| `ERR unavailable` | 区域语音播放功能不可用 |
| `ERR failed` | 机器人未能开始播放 |
| `ERR internal` | 机器人内部未能调度命令 |

读取该特征值返回最近一次通知文本（尚无通知时为当前状态）。`PLAYING` 只表示
已开始播放，不通过 BLE 传输音频数据。

### 12.3 标准交互时序

```text
App                                      Robot
 |---- Subscribe Zone Voice Notify ----->|
 |<------------------------------ IDLE --|
 |---- Write "LIST" -------------------->|
 |<-- LIST charging_zone.mp3 ... pool_zone.mp3
 |---- Write "PLAY pool_zone.mp3" ------>|
 |<--------------- PLAYING pool_zone.mp3 -|
 |              （音频播放中）              |
 |<------------------------------ IDLE --|  自然结束，机器人主动推送
```

## 13. 初始位姿设置（Initial Pose）

App 可以在机器人定位丢失或需要重新校准时，向 Initial Pose 特征值写入任意
非空内容，让机器人重新设置自己的初始位姿（重新定位）。**写入内容本身没有
含义**，只是“现在重新校准”的触发信号（例如写入 `1`）；位姿参数由机器人侧
固定，App 无法指定。

机器人收到写入后，向 ROS2 话题 `/initialpose` 发布一条
`geometry_msgs/PoseWithCovarianceStamped` 消息，后续重新定位行为由订阅该
话题的模块（如 AMCL）完成。消息内容由机器人固定（`frame_id` 为 `map`，
坐标为机器人约定的固定校准点，协方差同样固定），App 既不能指定也不需要通过
BLE 获取；具体数值见项目 README。

- 每次写入发布恰好一条消息；即使连续写入相同内容也会重复发布，机器人侧
  不去重。
- 写入内容不做格式校验（除空写入外），只按非空判断；换言之 App 写入任何
  非空文本（如 `1`）效果相同。
- 发布是即发即弃（fire-and-forget），写入的即时应答即最终结果：

| 通知内容 | 含义 |
|---|---|
| `OK` | 消息已发布到 `/initialpose` |
| `ERR encoding` | 写入内容不是合法 UTF-8 |
| `ERR command` | 空写入 |
| `ERR unavailable` | 初始位姿设置功能不可用 |
| `ERR internal` | 机器人内部未能调度命令 |

读取该特征值返回最近一次应答文本（尚无应答时为空）。`OK` 只表示消息已交给
ROS2 发布者，不表示机器人已完成重新定位或定位已收敛；重定位结果与收敛情况
不通过 BLE 上报。

## 14. 连接约束

- 当前版本按单个控制 App 设计。LAN TCP 新连接会踢掉旧连接。
- 当前版本不定义应用层鉴权、加密载荷或强制 BLE 配对；连接安全由内部测试环境负责。
- App 应设置合理的连接和写入超时。
- 蓝牙断开后应改用已建立的 LAN TCP 控制连接；TCP 也断了则等 BLE 恢复后重新
  Read LAN Endpoint 再连。

## 15. App 实现检查表

连接发现：

1. 扫描并按 Service UUID 筛选机器人。
2. 连接后 Read LAN Endpoint，得到 `<ipv4> 4205`（或配置的控制口）；值为
   `UNKNOWN` 时不要连 TCP。
3. Read Audio Endpoint，得到 PCM 下发地址；`UNKNOWN` 时不要发送音频。
4. 需要兜底时按 LAN Endpoint 建立 TCP，协议见 LAN_CONTROL_PROTOCOL.md。
5. 订阅 Notify 时注意 Android 15 大约 15 路上限；不要给 LAN/Audio 开 Notify
   （这两个特征值也没有 Notify）。

状态监测：

6. 读取或订阅 Battery Status 特征值；值为 `UNKNOWN` 或字段为 `UNKNOWN` 时按未知显示。
7. 读取或订阅 CPU Status 特征值；值为 `UNKNOWN` 时显示为使用率未知。
8. 读取或订阅 Network Status 特征值；`UNKNOWN` 显示为网络状态未知，`DISCONNECTED`
   显示为未连接。IP 变化以该特征值的 Notify 为准，然后可再 Read LAN/Audio。
9. 读取或订阅 Bandwidth Status 特征值；值为 `UNKNOWN` 时显示为速率未知。
10. 读取或订阅 Memory Status 特征值；值为 `UNKNOWN` 时显示为内存占用未知。

行为控制：

11. 使用机器人行为控制时，先订阅 Robot Control 特征值的 Notify 再写入命令；
    成功无通知，收到 `ERR ...` 时按错误码提示用户。

WiFi 配网：

12. 配网前先订阅 WiFi Config 特征值的 Notify，写入前协商足够的 MTU。
13. 写入格式为 `<ssid>\n<password>` 两行；收到 `CONNECTING` 后等待最终结果，
    以 `CONNECTED`/`FAILED` 为准更新界面，不要依据 `CONNECTING` 假定连接成功。
14. 收到 `ERR busy` 时提示用户等待上一次配网结束；配网期间不要重复写入。

导航任务：

15. 使用导航任务前，先订阅 Nav Task 特征值的 Notify，以收到的 `STATE ...` 为准
    更新界面；`START navigation` 前先确认 localization 为 `RUNNING`。
16. `STARTED` 不代表导航就绪；任务异常退出以 `EXITED` 通知为准提示用户。

区域导航：

17. 向 Zone Nav 特征值写入四个区域名之一（`charging_zone`、`mowing_zone`、
    `pool_zone`、`equipment_zone`）；以 `OK <zone>` 确认消息已发布，
    收到 `ERR ...` 时按错误码提示用户。

速度控制：

18. 向 Cmd Vel 特征值写入 `<linear_x> <angular_z>`（如 `-0.30 0.0`）；相同值
    每次写入都会再次发布。停止写入后机器人会因运动模块的消息超时保护自动
    停车，需要立即停车时显式写入 `0 0`；收到 `ERR ...` 时按错误码提示用户。

区域语音：

19. 使用区域语音前，先订阅 Zone Voice 特征值的 Notify，以收到的 `PLAYING` /
    `IDLE` / `UNKNOWN` 为准更新界面；播放前先 `LIST` 获取文件名，再写入
    `PLAY <file>`（文件名必须与列表完全一致，含扩展名、区分大小写）。
    自然结束以机器人主动推送的 `IDLE` 为准，不必轮询 `STATUS`。

初始位姿设置：

20. 需要重新校准时向 Initial Pose 特征值写入任意非空内容（如 `1`），以收到
    `OK` 确认消息已发布；位姿参数由机器人固定，App 不传也不显示坐标。
    `OK` 不代表重定位已完成，界面应以重新定位是否收敛的实际效果为准。
