# 小智机器人局域网 TCP 控制协议

本文档定义手机 App 与小智机器人之间的局域网 TCP 控制协议。该通道是 BLE
GATT 控制通道的全功能兜底：功能、文本 payload 与
[BLE_CONTROL_PROTOCOL.md](BLE_CONTROL_PROTOCOL.md) 中对应特征值一致，仅传输层
换成一条持久 TCP 连接。

协议版本：`1.1`（相对 1.0 为兼容扩展：`ROBOT` 通道增加前进 / 后退 / 转向 /
跳舞 / 点头等命令名，与 BLE 协议 2.1 对齐）。

音频 PCM **不走本通道**。App 通过本协议或 BLE 查询 Audio 口之后，另开一条
裸 TCP 把 PCM 打到 `pcm_tcp_server`。

## 1. 适用范围

- App 先通过 BLE 连接机器人，读取 LAN Endpoint 特征值得到 `<ipv4> <port>`，
  再连到该地址。蓝牙断开后，已建立的 TCP 连接继续工作。
- 当前版本按单个控制 App 设计：新 TCP 连接会踢掉旧连接。
- BLE 与 TCP 可同时在线。写命令谁发谁生效；状态 Notify 会推到 TCP 客户端
  （BLE 侧仍走各自特征值的 Notify）。
- 不定义应用层鉴权；仅用于内网。

## 2. 连接

| 项目 | 值 |
|---|---|
| 传输 | TCP |
| 默认监听 | `0.0.0.0:4205`（机器人侧可配置） |
| 编码 | UTF-8 文本，一行一条，以 `\n` 结尾（`\r\n` 可接受） |
| 单行上限 | 180 字节（与 BLE 通知上限一致，超长由机器人截断） |

连接成功后，机器人立刻把当前缓存的各通道值各推一行（连接快照，含 `LAN` /
`AUDIO`）。之后只有带 Notify 语义的通道才会再主动推送；`LAN` / `AUDIO`
仅响应 `GET`（与 BLE 这两个特征值无 Notify 对齐）。IP 变化看 `NETWORK`
行里的 ip 字段。

## 3. 帧格式

App → 机器人：

```text
GET <CHANNEL>
<CHANNEL> <payload>
```

- `GET` 读取当前缓存。
- 第二行形式是写入，`<payload>` 与对应 BLE 特征值的写入内容相同（WiFi 除外，见第 5 节）。
- 通道名大小写不敏感。

机器人 → App：

```text
<CHANNEL> <payload>
ERR <reason>
ERR <CHANNEL> <reason>
```

| 行 | 含义 |
|---|---|
| `<CHANNEL> <payload>` | 读应答、写应答，或该通道的主动 Notify |
| `ERR empty` | 空行 |
| `ERR command` | `GET` 缺少通道名 |
| `ERR unknown` / `ERR <TOKEN> unknown` | 无法识别的通道 |
| `ERR <CHANNEL> readonly` | 向只读通道写入 |
| `ERR encoding` | 一行不是合法 UTF-8 |

## 4. 通道

| CHANNEL | 对应 BLE 特征值 | 写入 | 连接后主动推送 |
|---|---|---|---|
| `LAN` | LAN Endpoint | 否 | 否（仅快照 / GET） |
| `AUDIO` | Audio Endpoint | 否 | 否（仅快照 / GET） |
| `BATTERY` | Battery Status | 否 | 是（与 BLE 相同，约 1 秒） |
| `NETWORK` | Network Status | 否 | 是（变化时） |
| `CPU` | CPU Status | 否 | 是（超阈值） |
| `BANDWIDTH` | Bandwidth Status | 否 | 是（超阈值） |
| `MEMORY` | Memory Status | 否 | 是（超阈值） |
| `ROBOT` | Robot Control | 命令名 | 失败时；成功时 TCP 额外回 `ROBOT OK <cmd>`（BLE 成功静默，TCP 需要确认写已被接受） |
| `WIFI` | WiFi Config | 见第 5 节 | 是 |
| `NAV` | Nav Task | `START` / `STOP` / `STATUS` | 是（含异步 STOPPED/EXITED） |
| `ZONE_NAV` | Zone Nav | 区域名 | 写入应答 |
| `CMD_VEL` | Cmd Vel | `<linear_x> <angular_z>` | 写入应答；客户端不必等待（对标 BLE Write Without Response） |
| `ZONE_VOICE` | Zone Voice | LIST / PLAY / STOP / STATUS | 是（含状态变化） |
| `INITIAL_POSE` | Initial Pose | 任意非空 | 写入应答 |

`<payload>` 的文本格式与 BLE 对应特征值完全相同，见 BLE 协议各节。例如：

```text
> GET AUDIO
< AUDIO 192.168.1.12 4203

> GET LAN
< LAN 192.168.1.12 4205

> ROBOT stand_up
< ROBOT OK stand_up

> ROBOT go_forward
< ROBOT OK go_forward

> ROBOT nod
< ROBOT OK nod

> CMD_VEL -0.30 0.0
< CMD_VEL OK -0.30 0.0
```

无 IPv4 或音频查询失败时：`AUDIO UNKNOWN`、`LAN UNKNOWN`。

## 5. WiFi 写入

BLE 上 WiFi Config 是两行文本 `<ssid>\n<password>`。TCP 一行一条，因此 WiFi
写入用通道名单独一行，随后两行分别是 SSID 和密码：

```text
WIFI
<ssid>
<password>
```

空密码（第二行为空）表示开放网络。SSID 可以含空格。应答与 BLE 相同：
`WIFI CONNECTING <ssid>`，随后 `WIFI CONNECTED <ssid>` 或 `WIFI FAILED ...`。

## 6. 查询音频下发地址

```text
> GET AUDIO
< AUDIO 192.168.1.12 4203
```

App 把 16-bit 小端、单声道、24000 Hz 的裸 PCM 打到该 `IP:port`（默认 4203），
不要打到控制口 4205，也不要打 pcm_tcp_server 的查询口（默认 4204）。

## 7. 标准交互时序

```text
App                                      Robot
 |---- BLE 连接，Read LAN Endpoint ------>|
 |<-------------- 192.168.1.12 4205 ------|
 |---- TCP connect 192.168.1.12:4205 ---->|
 |<---------------- LAN 192.168.1.12 4205 -|  快照
 |<------------- AUDIO 192.168.1.12 4203 --|
 |<---------------- BATTERY 0.670 CHARGING -|
 |                    …                    |
 |---- GET AUDIO ------------------------>|
 |<------------- AUDIO 192.168.1.12 4203 --|
 |---- PCM TCP 192.168.1.12:4203 -------->|  独立连接，非本协议
```

蓝牙断开不影响已建立的 TCP 控制连接。TCP 断开后 App 可在仍有 BLE 时重新
Read LAN Endpoint 再连，或直接重连上次的地址。
