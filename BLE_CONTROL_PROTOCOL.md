# 小智机器人 BLE 按住说话控制协议

本文档定义手机 App 与小智机器人之间的 BLE（Bluetooth Low Energy）控制协议。
协议用于实现与 Web 页面相同的“按住说话”行为：按钮按下开始收音，按钮松开结束收音。

协议版本：`1.2`

## 1. 适用范围

- 本协议定义手机 App 与机器人之间的 BLE 控制通道，由机器人侧实现。
  App 只与本协议交互，不感知机器人内部实现。
- BLE 只传输控制命令和机器人状态，不通过 BLE 传输音频。

## 2. GATT 定义

| 项目 | 值 |
|---|---|
| 广播名称默认值 | `Xiaozhi` |
| Service UUID | `12345678-1234-5678-1234-56789abcdef0` |
| Command Characteristic UUID | `12345678-1234-5678-1234-56789abcdef1` |
| WebSocket URL Characteristic UUID | `12345678-1234-5678-1234-56789abcdef2` |
| Error Characteristic UUID | `12345678-1234-5678-1234-56789abcdef3` |
| Battery Status Characteristic UUID | `12345678-1234-5678-1234-56789abcdef4` |
| Network Status Characteristic UUID | `12345678-1234-5678-1234-56789abcdef5` |
| CPU Status Characteristic UUID | `12345678-1234-5678-1234-56789abcdef6` |
| Command Characteristic 属性 | Read、Write、Notify |
| WebSocket URL Characteristic 属性 | Read、Write、Notify |
| Error Characteristic 属性 | Read、Notify |
| Battery Status Characteristic 属性 | Read、Notify |
| Network Status Characteristic 属性 | Read、Notify |
| CPU Status Characteristic 属性 | Read、Notify |

广播名称可在机器人侧配置修改。App 应以 Service UUID 识别设备，不应只依赖广播名称。

## 3. 数据格式

- App 写入和机器人通知均为 UTF-8 文本。
- 每条消息只包含一条完整指令或响应，不添加 JSON 包装。
- App 写入时可以带首尾空白；机器人解析时会去除首尾空白并忽略命令大小写。
- Command 特征值中的命令、响应和状态均不超过默认 ATT MTU 下单包可承载的 20 字节；URL 和
  Error 特征值按 180 字节上限处理。
- App 应优先使用 GATT Write With Response，确认写入已被 BLE 协议栈接收。
- URL 特征值使用 UTF-8 文本，最大 180 字节；只接受 `ws://` 或 `wss://`，且必须包含主机名。
- App 应在写 URL 前协商至少 183 字节的 ATT MTU（有效载荷为 MTU 减 3，通常请求 MTU 247）。
- Battery Status 特征值使用 UTF-8 文本，格式为 `<percentage> <supply_status>`，例如
  `0.670 CHARGING`；尚未获取到电池状态时为 `UNKNOWN`。
- Network Status 特征值使用 UTF-8 文本，格式见第 9 节。
- CPU Status 特征值使用 UTF-8 文本，格式为 `CPU <usage>`（`usage` 为整机 CPU 使用率
  百分比，保留一位小数），例如 `CPU 23.5`；尚未获取到读数时为 `UNKNOWN`。

## 4. App 发给机器人

| 写入内容 | 触发时机 | 机器人行为 |
|---|---|---|
| `start` | 按住说话按钮时 | 开始收音；若 AI 正在播放，则打断播放并开始收音 |
| `stop` | 松开或取消按钮操作时 | 结束收音，并把本次语音交给服务端处理 |

`start` 和 `stop` 是幂等指令：重复发送不会开启多个录音会话，也不会导致协议错误。

## 5. 机器人发给 App

App 必须订阅 Command Characteristic 的 Notify。订阅成功后，机器人立即发送当前状态。

### 5.1 命令接收结果

| 通知内容 | 含义 |
|---|---|
| `OK start` | `start` 已接受并调度 |
| `OK stop` | `stop` 已接受并调度 |
| `ERR encoding` | 写入内容不是合法 UTF-8 |
| `ERR command` | 不支持该命令 |
| `ERR internal` | 机器人内部未能调度命令 |

`OK` 表示命令已被机器人控制层接受，不表示状态切换已经完成。App 应通过后续的
`STATE ...` 通知确认实际状态。

### 5.2 机器人状态

| 通知内容 | 含义 |
|---|---|
| `STATE idle` | 空闲，当前没有收音或播放 |
| `STATE connecting` | 正在连接语音服务端 |
| `STATE listening` | 正在收音 |
| `STATE speaking` | AI 正在播放语音 |

App 不应根据 `OK start` 直接假定机器人已进入 `listening`，也不应根据 `OK stop`
直接假定机器人已进入 `idle` 或 `speaking`。

## 6. WebSocket URL 配置

App 应发现并订阅 WebSocket URL 特征值。订阅成功后，机器人立即通知当前生效地址；读取该
特征值也会返回当前生效地址。

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

## 7. Error 特征值

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

## 8. Battery Status 特征值（电池状态）

App 可以读取或订阅 Battery Status 特征值来获取机器人的电池状态。

- 通知周期为 1 秒（重发最近一次获取到的缓存值）。
- 特征值只读，App 不应向该特征值写入。
- 值为 UTF-8 文本，格式为 `<percentage> <supply_status>`，两个字段以单个空格分隔：
  - `percentage` 为电量百分比的小数形式（0 到 1），例如 `0.670` 表示电量 67%；
    尚未收到有效读数时为 `UNKNOWN`。
  - `supply_status` 为供电状态：`CHARGING`（充电中）、`DISCHARGING`（放电中）、
    `NOT_CHARGING`（未在充电）、`FULL`（已充满）；尚未收到有效读数时为 `UNKNOWN`。
- 两个字段都尚未获取到时，整个值为 `UNKNOWN`（例如机器人未配置电池信息源）。

示例：`0.670 CHARGING`、`0.982 FULL`、`UNKNOWN DISCHARGING`、`UNKNOWN`。

## 9. Network Status 特征值（网络状态）

App 可以读取或订阅 Network Status 特征值来获取机器人的 WiFi 连接状态。

- 仅在状态变化时通知；订阅成功后立即收到当前缓存值。
- 特征值只读，App 不应向该特征值写入。
- 值为 UTF-8 文本，可能取值：

| 值 | 含义 |
|---|---|
| `UNKNOWN` | 尚未获取到网络状态（机器人刚启动或没有无线网卡） |
| `DISCONNECTED` | WiFi 未连接 |
| `WIFI <rssi> <ip> <ssid>` | WiFi 已连接；`rssi` 为信号强度（整数，单位 dBm），`ip` 为 IPv4 地址，`ssid` 为剩余全部文本 |

`ssid` 可能包含空格，App 解析时应先取前三个字段（`WIFI`、`rssi`、`ip`），其余部分
作为 SSID。`rssi` 或 `ip` 为 `-` 时表示该字段未知。

示例：`WIFI -38 172.16.0.195 MyHome`、`WIFI -52 - Office AP 2F`。

## 10. CPU Status 特征值（CPU 使用率）

App 可以读取或订阅 CPU Status 特征值来获取机器人的整机 CPU 使用率。

- 使用率没有瞬时值，机器人启动后首个读数需要约一个采样周期才能产生；此前值为 `UNKNOWN`。
- 仅在使用率变化达到阈值（默认 1 个百分点）时通知，避免 CPU 抖动导致频繁
  推送；订阅成功后立即收到当前缓存值。
- 特征值只读，App 不应向该特征值写入。
- 值为 UTF-8 文本，格式为 `CPU <usage>`，`usage` 为 0-100 的百分比、保留一位
  小数，例如 `CPU 23.5`；尚未获取到读数时为 `UNKNOWN`。

## 11. 标准交互时序

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

## 12. 断连与异常处理

### 12.1 机器人行为

- 机器人只在 App 已订阅 Notify 后接受 `start`。
- 接受 `start` 后，机器人记录一次“按钮仍按下”的 BLE 控制状态。
- 收到 `stop` 后清除该状态。
- 如果按钮仍处于按下状态时发生以下任一情况，机器人自动执行一次 `stop`：
  - BLE 连接断开；
  - App 取消 Notify 订阅；
  - 机器人侧 BLE 服务关闭或进程退出。
- 自动 `stop` 是安全兜底，用于处理 App 崩溃、手机离开通信范围或松开消息丢失。

### 12.2 App 行为

App 应在以下所有场景主动写入 `stop`，不能只处理普通的手指抬起事件：

- 按钮 `touch up`；
- 触摸被系统取消；
- 手指移出按钮且产品交互定义为取消；
- App 进入后台；
- 页面退出或控制组件销毁；
- App 主动断开 BLE 连接之前。

连接意外断开后，App 应立即把本地按钮恢复为未按下状态。重连流程必须重新发现服务、
重新订阅 Notify，并以机器人最新的 `STATE` 为准；App 不得在重连后自动补发旧的 `start`。

## 13. 连接约束

- 当前版本按单个控制 App 设计，不支持多个 App 同时争用按住说话控制权。
- 当前版本不定义应用层鉴权、加密载荷或强制 BLE 配对；连接安全由内部测试环境负责。
- WebSocket URL 特征值允许普通 BLE 连接直接读写，便于内部测试快速切换服务端地址。
- App 应设置合理的连接和写入超时。一次写入失败时，应将按钮恢复为未按下状态；若连接仍然
  有效，可以补发一次 `stop`，但不应自动重试 `start`。

## 14. App 实现检查表

1. 扫描并按 Service UUID 筛选机器人。
2. 连接后发现 Service 和 Command Characteristic。
3. 先订阅 Notify，收到首条 `STATE ...` 后再启用按住说话按钮。
4. 按下时写入 `start`，松开或取消时写入 `stop`。
5. 用 `STATE ...` 驱动页面状态，用 `OK ...` / `ERR ...` 处理单次命令结果。
6. 断连时复位按钮；重连后不恢复上一次按下状态。
7. 订阅 URL 特征值和 Error 特征值；收到 URL Notify 后更新当前服务器显示。
8. 修改 URL 前避免在 `listening`/`speaking` 状态下写入（会被 `CONFIG_BUSY` 拒绝）；
   `idle` 和 `connecting` 状态下可直接写入，写入后等待 URL Notify 和 Error 特征值结果。
9. URL 写入前协商足够的 MTU。
10. 读取或订阅 Battery Status 特征值；值为 `UNKNOWN` 或字段为 `UNKNOWN` 时按未知显示。
11. 读取或订阅 Network Status 特征值；`UNKNOWN` 显示为网络状态未知，`DISCONNECTED`
    显示为未连接。
12. 读取或订阅 CPU Status 特征值；值为 `UNKNOWN` 时显示为使用率未知。
