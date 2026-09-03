# 小智机器人 BLE 按住说话控制协议

本文档定义手机 App 与小智机器人之间的 BLE（Bluetooth Low Energy）控制协议。
协议用于实现与 Web 页面相同的“按住说话”行为：按钮按下开始收音，按钮松开结束收音。

协议版本：`1.10`

## 1. 适用范围

- 本协议定义手机 App 与机器人之间的 BLE 控制通道，由机器人侧实现。
  App 只与本协议交互，不感知机器人内部实现。
- BLE 只传输控制命令和机器人状态，不通过 BLE 传输音频。

## 2. GATT 定义

| 项目 | 值 |
|---|---|
| 广播名称默认值 | `Xiaozhi` |
| Service UUID | `12345678-1234-5678-1234-56789abcdef0` |

Service 内的特征值按功能分为五类，UUID 均为 `12345678-1234-5678-1234-56789abcdefN`：

| 类别 | 特征值 | UUID 尾号 | 属性 |
|---|---|---|---|
| 语音控制 | Command Characteristic | `abcdef1` | Read、Write、Notify |
| 连接配置 | WebSocket URL Characteristic | `abcdef2` | Read、Write、Notify |
| 连接配置 | Error Characteristic | `abcdef3` | Read、Notify |
| 状态监测 | Battery Status Characteristic | `abcdef4` | Read、Notify |
| 状态监测 | Network Status Characteristic | `abcdef5` | Read、Notify |
| 状态监测 | CPU Status Characteristic | `abcdef6` | Read、Notify |
| 状态监测 | Bandwidth Status Characteristic | `abcdef7` | Read、Notify |
| 状态监测 | Latency Status Characteristic | `abcdef8` | Read、Notify |
| 行为控制 | Robot Control Characteristic | `abcdef9` | Read、Write、Notify |
| 连接配置 | WiFi Config Characteristic | `abcdefa` | Read、Write、Notify |
| 任务控制 | Nav Task Characteristic | `abcdefb` | Read、Write、Notify |
| 任务控制 | Zone Nav Characteristic | `abcdefc` | Read、Write、Notify |
| 运动控制 | Cmd Vel Characteristic | `abcdefd` | Read、Write、Notify |
| 状态监测 | Memory Status Characteristic | `abcdefe` | Read、Notify |

广播名称可在机器人侧配置修改。App 应以 Service UUID 识别设备，不应只依赖广播名称。

## 3. 数据格式

- App 写入和机器人通知均为 UTF-8 文本。
- 每条消息只包含一条完整指令或响应，不添加 JSON 包装。
- App 写入时可以带首尾空白；机器人解析时会去除首尾空白并忽略命令大小写。
- Command 特征值中的命令、响应和状态均不超过默认 ATT MTU 下单包可承载的 20 字节；
  其余特征值按 180 字节上限处理，超长文本由机器人侧截断。
- App 应优先使用 GATT Write With Response，确认写入已被 BLE 协议栈接收；
  需要高频连续写入的 Cmd Vel 特征值除外（见第 12 节）。
- 状态监测类特征值在数据尚未获取到时统一报 `UNKNOWN`；字段级缺失用占位符（如 `-`）。
- 各特征值的具体文本格式见对应章节：Command 见第 4 节，WebSocket URL 见第 5 节，
  Error 见第 6 节，状态监测各特征值见第 7 节，Robot Control 见第 8 节，
  WiFi Config 见第 9 节，Nav Task 见第 10 节，Zone Nav 见第 11 节，
  Cmd Vel 见第 12 节。

## 4. 语音控制（按住说话）

### 4.1 控制命令（App → 机器人）

App 向 Command 特征值写入控制命令：

| 写入内容 | 触发时机 | 机器人行为 |
|---|---|---|
| `start` | 按住说话按钮时 | 开始收音；若 AI 正在播放，则打断播放并开始收音 |
| `stop` | 松开或取消按钮操作时 | 结束收音，并把本次语音交给服务端处理 |

`start` 和 `stop` 是幂等指令：重复发送不会开启多个录音会话，也不会导致协议错误。

### 4.2 状态与应答（机器人 → App）

App 必须订阅 Command Characteristic 的 Notify。订阅成功后，机器人立即发送当前状态。

#### 4.2.1 命令接收结果

| 通知内容 | 含义 |
|---|---|
| `OK start` | `start` 已接受并调度 |
| `OK stop` | `stop` 已接受并调度 |
| `ERR encoding` | 写入内容不是合法 UTF-8 |
| `ERR command` | 不支持该命令 |
| `ERR internal` | 机器人内部未能调度命令 |

`OK` 表示命令已被机器人控制层接受，不表示状态切换已经完成。App 应通过后续的
`STATE ...` 通知确认实际状态。

#### 4.2.2 机器人状态

| 通知内容 | 含义 |
|---|---|
| `STATE idle` | 空闲，当前没有收音或播放 |
| `STATE connecting` | 正在连接语音服务端 |
| `STATE listening` | 正在收音 |
| `STATE speaking` | AI 正在播放语音 |

App 不应根据 `OK start` 直接假定机器人已进入 `listening`，也不应根据 `OK stop`
直接假定机器人已进入 `idle` 或 `speaking`。

### 4.3 标准交互时序

```text
App                                      Robot
 |                                         |
 |---- Scan by Service UUID -------------->|
 |---- Connect ---------------------------->|
 |---- Subscribe Command Notify ---------->|
 |<-------------------------- STATE idle --|
 |                                         |
 |  用户按下按钮                            |
 |---- Write "start" --------------------->|
 |<----------------------------- OK start --|
 |<---------------------- STATE listening --|
 |                                         |
 |  用户松开按钮                            |
 |---- Write "stop" ---------------------->|
 |<------------------------------ OK stop --|
 |<--------------------------- STATE idle --|
 |<----------------------- STATE speaking --|  取决于服务端响应
```

状态可能因网络和语音服务端行为出现不同的合法序列，App 应按收到的最后一个
`STATE` 更新界面，不应写死 `stop` 后必然立刻进入某个状态。

### 4.4 断连与异常处理

#### 4.4.1 机器人行为

- 机器人只在 App 已订阅 Notify 后接受 `start`。
- 接受 `start` 后，机器人记录一次“按钮仍按下”的 BLE 控制状态。
- 收到 `stop` 后清除该状态。
- 如果按钮仍处于按下状态时发生以下任一情况，机器人自动执行一次 `stop`：
  - BLE 连接断开；
  - App 取消 Notify 订阅；
  - 机器人侧 BLE 服务关闭或进程退出。
- 自动 `stop` 是安全兜底，用于处理 App 崩溃、手机离开通信范围或松开消息丢失。

#### 4.4.2 App 行为

App 应在以下所有场景主动写入 `stop`，不能只处理普通的手指抬起事件：

- 按钮 `touch up`；
- 触摸被系统取消；
- 手指移出按钮且产品交互定义为取消；
- App 进入后台；
- 页面退出或控制组件销毁；
- App 主动断开 BLE 连接之前。

连接意外断开后，App 应立即把本地按钮恢复为未按下状态。重连流程必须重新发现服务、
重新订阅 Notify，并以机器人最新的 `STATE` 为准；App 不得在重连后自动补发旧的 `start`。

## 5. WebSocket URL 配置

App 应发现并订阅 WebSocket URL 特征值。订阅成功后，机器人立即通知当前生效地址；读取该
特征值也会返回当前生效地址。

URL 特征值使用 UTF-8 文本，最大 180 字节；只接受 `ws://` 或 `wss://`，且必须包含
主机名。App 应在写 URL 前协商至少 183 字节的 ATT MTU（有效载荷为 MTU 减 3，通常
请求 MTU 247）。

App 直接向该特征值写入完整 URL，例如：

```text
wss://robot.example.com:10000/ws/robot
```

写入响应只表示机器人已经接收并开始处理，不表示新地址已经连接成功。机器人会：

1. 校验并持久化保存 URL。
2. 关闭当前 WebSocket，更新地址并立即尝试重新连接。
3. 持久化成功后通过 URL 特征值 Notify 新地址；连接失败则通过 Error 特征值报告错误。

新地址暂时不可用时，机器人保留该地址并继续后台重连；App 仍可通过 BLE 写入新的地址，
无需等待重连成功。

如果当前状态为 `listening` 或 `speaking`，配置会被拒绝并报告
`CONFIG_BUSY`。App 应等待 `STATE idle` 或 `STATE connecting` 后再修改地址；
`connecting` 状态下写入会被接受，机器人会中断当前重连尝试并改用新地址。

机器人侧可能通过自身配置锁定 URL（锁定地址优先于 BLE 写入）。此时写入会报告
`CONFIG_LOCKED`，读取仍返回锁定的生效地址。

## 6. Error 特征值（错误上报）

App 应订阅 Error 特征值。订阅后会立即收到最近一次错误，当前没有错误时为 `NONE`。错误
通知格式为：

```text
ERROR <code> <message>
```

当前错误码包括：

| 错误码 | 含义 |
|---|---|
| `WS_ERROR` | WebSocket 建连、接收、Ping 或连接超时失败 |
| `VOICE_UNAVAILABLE` | 机器人语音功能不可用（语音服务未就绪） |
| `CONFIG_ENCODING` | URL 不是合法 UTF-8 |
| `CONFIG_INVALID` | URL 为空或不是合法 `ws`/`wss` 地址 |
| `CONFIG_BUSY` | 机器人正在收音或播放（`listening`/`speaking`） |
| `CONFIG_LOCKED` | URL 被机器人侧配置锁定 |
| `CONFIG_SAVE` | 覆盖文件无法写入 |
| `CONFIG_APPLY` | URL 已保存但运行时应用失败 |
| `CONFIG_INTERNAL` | BLE 配置请求无法调度 |

WebSocket 恢复连接后会通知 `NONE`，App 可据此清除错误提示。Error 特征值的通知最大为
180 字节，过长的底层异常文本会被截断。

## 7. 状态监测

状态监测类特征值均为只读（Read、Notify），App 不应写入；订阅成功后立即收到当前
缓存值，之后按各自的通知策略推送。电池、CPU 和内存属于机身状态，WiFi 连接、带宽和
服务器延迟属于网络状态。

### 7.1 电池状态（Battery Status）

App 可以读取或订阅 Battery Status 特征值来获取机器人的电池状态。

- 通知周期为 1 秒（重发最近一次获取到的缓存值）。
- 值为 UTF-8 文本，格式为 `<percentage> <supply_status>`，两个字段以单个空格分隔：
  - `percentage` 为电量百分比的小数形式（0 到 1），例如 `0.670` 表示电量 67%；
    尚未收到有效读数时为 `UNKNOWN`。
  - `supply_status` 为供电状态：`CHARGING`（充电中）、`DISCHARGING`（放电中）、
    `NOT_CHARGING`（未在充电）、`FULL`（已充满）；尚未收到有效读数时为 `UNKNOWN`。
- 两个字段都尚未获取到时，整个值为 `UNKNOWN`（例如机器人未配置电池信息源）。

示例：`0.670 CHARGING`、`0.982 FULL`、`UNKNOWN DISCHARGING`、`UNKNOWN`。

### 7.2 CPU 使用率（CPU Status）

App 可以读取或订阅 CPU Status 特征值来获取机器人的整机 CPU 使用率。

- 使用率没有瞬时值，机器人启动后首个读数需要约一个采样周期才能产生；此前值为 `UNKNOWN`。
- 仅在使用率变化达到阈值（默认 1 个百分点）时通知，避免 CPU 抖动导致频繁推送。
- 值为 UTF-8 文本，格式为 `CPU <usage>`，`usage` 为 0-100 的百分比、保留一位
  小数，例如 `CPU 23.5`；尚未获取到读数时为 `UNKNOWN`。

### 7.3 网络状态

网络状态由三个特征值组成：WiFi 连接状态（连没连、信号、IP、SSID）、WiFi 带宽速率
（实时上行/下行吞吐）和对话服务器延迟（到语音服务端的通信延迟）。

#### 7.3.1 WiFi 连接状态（Network Status）

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

#### 7.3.2 WiFi 带宽速率（Bandwidth Status）

App 可以读取或订阅 Bandwidth Status 特征值来获取机器人 WiFi 网卡的实时吞吐速率。

- 速率没有瞬时值，机器人启动后首个读数需要约一个采样周期才能产生；此前值为 `UNKNOWN`。
- 仅在 rx 或 tx 变化达到阈值（默认 10 KB/s）时通知。
- 值为 UTF-8 文本，格式为 `BANDWIDTH <rx_kbps> <tx_kbps>`：
  - `rx_kbps` 为下行速率，单位 KB/s，保留一位小数；
  - `tx_kbps` 为上行速率，单位 KB/s，保留一位小数。
- 尚未获取到读数（机器人刚启动或没有无线网卡）时整个值为 `UNKNOWN`。

示例：`BANDWIDTH 1234.5 56.7`、`UNKNOWN`。

#### 7.3.3 对话服务器延迟（Latency Status）

App 可以读取或订阅 Latency Status 特征值来获取机器人到对话服务器（WebSocket 地址
对应的主机）的通信延迟。机器人侧按周期向该主机发起 TCP 建连，以建连耗时近似
`ping` 延迟。

- 仅在延迟变化达到阈值（默认 10 毫秒）或可达性变化时通知。
- 值为 UTF-8 文本，可能取值：

| 值 | 含义 |
|---|---|
| `UNKNOWN` | 尚未获取到读数（尚未从对话模块同步到服务器地址，或机器人刚启动） |
| `LATENCY <ms>` | 服务器可达；`ms` 为建连延迟，整数毫秒 |
| `LATENCY -` | 服务器不可达（连接被拒绝或超时） |

示例：`LATENCY 37`、`LATENCY -`、`UNKNOWN`。

### 7.4 内存占用（Memory Status）

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

## 8. 机器人行为控制（Robot Control）

App 可以向 Robot Control 特征值写入命令名，控制机器人执行预配置的行为（如站立、
蹲下）。机器人侧维护一张“命令名 → 行为”的映射表，写入的命令名查表后异步执行。

- App 写入内容为命令名（UTF-8 文本），例如 `stand_up`、`lie_down`；机器人解析时
  去除首尾空白并忽略大小写。
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

## 9. WiFi 配网（WiFi Config）

App 可以向 WiFi Config 特征值写入目标 WiFi 的 SSID 和密码，机器人连接指定
WiFi，并把连接结果通过 Notify 异步上报。机器人侧保存该 WiFi 配置，重启网络后
自动重连。

### 9.1 写入格式（App → 机器人）

写入内容为 UTF-8 文本两行，以单个换行符 `\n` 分隔：

```text
<ssid>\n<password>
```

- 第一行为 SSID，整行即 SSID（SSID 可能包含空格）；不能为空，按 IEEE 802.11 上限
  不超过 32 字节。
- 第二行为密码；空行或省略第二行均表示连接开放（无密码）网络。WPA/WPA2 密码为
  8-63 个字符。
- 尾部换行可选；机器人解析时会去除各行首尾空白。
- 写入总长不超过 180 字节；App 应在写入前协商至少 183 字节的 ATT MTU（同 URL 特征值）。

### 9.2 应答与结果（机器人 → App）

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

### 9.3 标准交互时序

```text
App                                      Robot
 |---- Subscribe WiFi Config Notify ---->|
 |---- Write "MyHome\n12345678" -------->|
 |<------------------ CONNECTING MyHome --|
 |              （机器人切换 WiFi 中）     |
 |<------------------- CONNECTED MyHome --|
 |<--- Network Status: WIFI -42 ... MyHome|  由状态监测特征值另行上报
```

## 10. 导航任务控制（Nav Task）

App 可以向 Nav Task 特征值写入命令，在机器人上启动或停止两个固定的导航
任务：`localization`（定位）和 `navigation`（导航）。两个任务的默认参数
由机器人侧固定（见 10.2），App 只能在白名单内覆盖参数。

- 机器人侧服务正常停止（含服务重启）时会先停止仍在运行的任务。
- 任务输出不通过 BLE 转发。
- 写入/通知均为 UTF-8 文本，通知不超过 180 字节；带参数覆盖的写入可能较长，写入前
  建议协商至少 183 字节的 ATT MTU（同 URL 特征值）。机器人解析时去除首尾空白，
  命令动词与任务名忽略大小写；参数 key 小写规范化，value 原样保留。

### 10.1 写入命令（App → 机器人）

| 写入内容 | 含义 |
|---|---|
| `START <task>` | 以默认参数启动任务 |
| `START <task> key=value ...` | 覆盖部分参数后启动任务（未覆盖的 key 用默认值） |
| `STOP <task>` | 停止任务 |
| `STATUS` | 查询两个任务的当前状态 |

- `<task>` 为 `localization` 或 `navigation`。
- `key=value` 之间以空白分隔；key 必须在 10.2 的白名单内；value 不能为空、不能含
  空白字符。
- `START navigation` 要求 `localization` 处于 `RUNNING`，否则拒绝（见
  `ERR state localization not_running`）；机器人不会自动代起 localization，
  启动顺序由 App 控制。

### 10.2 可覆盖参数与默认值

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

### 10.3 应答与状态通知（机器人 → App）

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

### 10.4 标准交互时序

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

## 11. 区域导航（Zone Nav）

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

## 12. 速度控制（Cmd Vel）

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

## 13. 连接约束

- 当前版本按单个控制 App 设计，不支持多个 App 同时争用按住说话控制权。
- 当前版本不定义应用层鉴权、加密载荷或强制 BLE 配对；连接安全由内部测试环境负责。
- WebSocket URL 特征值允许普通 BLE 连接直接读写，便于内部测试快速切换服务端地址。
- App 应设置合理的连接和写入超时。一次写入失败时，应将按钮恢复为未按下状态；若连接仍然
  有效，可以补发一次 `stop`，但不应自动重试 `start`。

## 14. App 实现检查表

语音控制：

1. 扫描并按 Service UUID 筛选机器人。
2. 连接后发现 Service 和 Command Characteristic。
3. 先订阅 Notify，收到首条 `STATE ...` 后再启用按住说话按钮。
4. 按下时写入 `start`，松开或取消时写入 `stop`。
5. 用 `STATE ...` 驱动页面状态，用 `OK ...` / `ERR ...` 处理单次命令结果。
6. 断连时复位按钮；重连后不恢复上一次按下状态。

连接配置：

7. 订阅 URL 特征值和 Error 特征值；收到 URL Notify 后更新当前服务器显示。
8. 修改 URL 前避免在 `listening`/`speaking` 状态下写入（会被 `CONFIG_BUSY` 拒绝）；
   `idle` 和 `connecting` 状态下可直接写入，写入后等待 URL Notify 和 Error 特征值结果。
9. URL 写入前协商足够的 MTU。

状态监测：

10. 读取或订阅 Battery Status 特征值；值为 `UNKNOWN` 或字段为 `UNKNOWN` 时按未知显示。
11. 读取或订阅 CPU Status 特征值；值为 `UNKNOWN` 时显示为使用率未知。
12. 读取或订阅 Network Status 特征值；`UNKNOWN` 显示为网络状态未知，`DISCONNECTED`
    显示为未连接。
13. 读取或订阅 Bandwidth Status 特征值；值为 `UNKNOWN` 时显示为速率未知。
14. 读取或订阅 Latency Status 特征值；`UNKNOWN` 显示为延迟未知，`LATENCY -`
    显示为服务器不可达。
15. 读取或订阅 Memory Status 特征值；值为 `UNKNOWN` 时显示为内存占用未知。

行为控制：

16. 使用机器人行为控制时，先订阅 Robot Control 特征值的 Notify 再写入命令；
    成功无通知，收到 `ERR ...` 时按错误码提示用户。

WiFi 配网：

17. 配网前先订阅 WiFi Config 特征值的 Notify，写入前协商足够的 MTU。
18. 写入格式为 `<ssid>\n<password>` 两行；收到 `CONNECTING` 后等待最终结果，
    以 `CONNECTED`/`FAILED` 为准更新界面，不要依据 `CONNECTING` 假定连接成功。
19. 收到 `ERR busy` 时提示用户等待上一次配网结束；配网期间不要重复写入。

导航任务：

20. 使用导航任务前，先订阅 Nav Task 特征值的 Notify，以收到的 `STATE ...` 为准
    更新界面；`START navigation` 前先确认 localization 为 `RUNNING`。
21. `STARTED` 不代表导航就绪；任务异常退出以 `EXITED` 通知为准提示用户。

区域导航：

22. 向 Zone Nav 特征值写入四个区域名之一（`charging_zone`、`mowing_zone`、
    `pool_zone`、`equipment_zone`）；以 `OK <zone>` 确认消息已发布，
    收到 `ERR ...` 时按错误码提示用户。

速度控制：

23. 向 Cmd Vel 特征值写入 `<linear_x> <angular_z>`（如 `-0.30 0.0`）；相同值
    每次写入都会再次发布。停止写入后机器人会因运动模块的消息超时保护自动
    停车，需要立即停车时显式写入 `0 0`；收到 `ERR ...` 时按错误码提示用户。
