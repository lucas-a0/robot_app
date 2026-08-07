# 小智机器人 BLE 按住说话控制协议

本文档定义手机 App 与小智机器人之间的 BLE（Bluetooth Low Energy）控制协议。
协议用于实现与 Web 页面相同的“按住说话”行为：按钮按下开始收音，按钮松开结束收音。

协议版本：`1.5`

## 1. 适用范围

- 本协议定义手机 App 与机器人之间的 BLE 控制通道，由机器人侧实现。
  App 只与本协议交互，不感知机器人内部实现。
- BLE 只传输控制命令和机器人状态，不通过 BLE 传输音频。

## 2. GATT 定义

| 项目 | 值 |
|---|---|
| 广播名称默认值 | `Xiaozhi` |
| Service UUID | `12345678-1234-5678-1234-56789abcdef0` |

Service 内的特征值按功能分为四类，UUID 均为 `12345678-1234-5678-1234-56789abcdefN`：

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

广播名称可在机器人侧配置修改。App 应以 Service UUID 识别设备，不应只依赖广播名称。

## 3. 数据格式

- App 写入和机器人通知均为 UTF-8 文本。
- 每条消息只包含一条完整指令或响应，不添加 JSON 包装。
- App 写入时可以带首尾空白；机器人解析时会去除首尾空白并忽略命令大小写。
- Command 特征值中的命令、响应和状态均不超过默认 ATT MTU 下单包可承载的 20 字节；
  其余特征值按 180 字节上限处理，超长文本由机器人侧截断。
- App 应优先使用 GATT Write With Response，确认写入已被 BLE 协议栈接收。
- 状态监测类特征值在数据尚未获取到时统一报 `UNKNOWN`；字段级缺失用占位符（如 `-`）。
- 各特征值的具体文本格式见对应章节：Command 见第 4 节，WebSocket URL 见第 5 节，
  Error 见第 6 节，状态监测各特征值见第 7 节，Robot Control 见第 8 节。

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
缓存值，之后按各自的通知策略推送。电池和 CPU 属于机身状态，WiFi 连接、带宽和
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

## 8. 机器人行为控制（Robot Control）

App 可以向 Robot Control 特征值写入命令名，控制机器人执行预配置的行为（如站立、
蹲下）。机器人侧维护一张“命令名 → ROS2 服务”的映射表（见
`config.yaml.example` 的 `robot_control.commands`），写入的命令名查表后异步调用
对应的 `std_srvs/Trigger` 服务。

- App 写入内容为命令名（UTF-8 文本），例如 `stand_up`、`lie_down`；机器人解析时
  去除首尾空白并忽略大小写。
- 命令执行**成功时完全静默**，不发送任何通知。
- 仅在失败时通过 Notify 上报错误，格式为 `ERR ...`；读取该特征值返回最近一次
  上报的错误（尚无错误时为空）：

| 通知内容 | 含义 |
|---|---|
| `ERR encoding` | 写入内容不是合法 UTF-8 |
| `ERR command` | 命令名未在机器人侧映射表中配置 |
| `ERR unavailable` | 机器人控制功能不可用（ROS 环境缺失或功能被禁用） |
| `ERR unavailable <command>` | 命令对应的 ROS 服务当前不在线 |
| `ERR failed <command> <message>` | 服务返回 `success=false`（`message` 为服务给出的原因）或调用发生异常 |
| `ERR timeout <command>` | 服务调用超过机器人侧配置的超时时间（默认 10 秒） |
| `ERR internal` | 机器人内部未能调度命令 |

行为类命令通常耗时数秒，错误上报是异步的：App 应先订阅该特征值的 Notify 再写入
命令，否则可能收不到错误通知。`ERR failed`/`ERR timeout` 中的 `<command>` 为机器人
实际执行的命令名（已规范化），便于 App 区分并发命令的结果。

## 9. 连接约束

- 当前版本按单个控制 App 设计，不支持多个 App 同时争用按住说话控制权。
- 当前版本不定义应用层鉴权、加密载荷或强制 BLE 配对；连接安全由内部测试环境负责。
- WebSocket URL 特征值允许普通 BLE 连接直接读写，便于内部测试快速切换服务端地址。
- App 应设置合理的连接和写入超时。一次写入失败时，应将按钮恢复为未按下状态；若连接仍然
  有效，可以补发一次 `stop`，但不应自动重试 `start`。

## 10. App 实现检查表

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

行为控制：

15. 使用机器人行为控制时，先订阅 Robot Control 特征值的 Notify 再写入命令；
    成功无通知，收到 `ERR ...` 时按错误码提示用户。
