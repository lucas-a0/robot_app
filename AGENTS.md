# AGENTS.md — 代码编写规范

本文档记录在本项目中编写代码时应遵循的经验和规范，供后续修改代码的协作者
（人或 AI）遵守。项目功能介绍见 [README.md](README.md)，BLE 协议定义见
[BLE_CONTROL_PROTOCOL.md](BLE_CONTROL_PROTOCOL.md)，本文不重复这些内容。

项目一句话：小智机器人的 BLE 控制桥接进程，向上（手机 App）暴露 BlueZ GATT
服务，向下对接对话模块的 Unix 控制 socket 和本机状态（ROS2 话题、内核接口）。

## 1. 避免"执行命令 + 解析输出"

数据采集**不要**用 `subprocess` 调外部命令再解析文本输出（ fragile、依赖部署
环境、systemd 下容易踩 PATH/环境变量问题）。本项目已淘汰的反面教材：
`ros2 topic echo` 轮询电量、`iw dev ... link` / `ip addr` 解析网络状态。

按优先级选择替代方案：

1. **内核接口直接读**：`/proc/stat`（CPU）、`/proc/meminfo`（内存）、
   `/proc/net/wireless`（信号强度）、ioctl（如 `SIOCGIWESSID` 取 SSID）。
2. **成熟库**：netifaces（IP 地址）、dasbus（BlueZ 与 NetworkManager 的
   D-Bus 接口）。
3. **订阅/调用而非轮询**：ROS2 数据用内置 rclpy 节点订阅话题或调用服务
   （见 `battery.py` / `robot_control.py`），不要 shell 出 `ros2` CLI。
   rclpy 的 init/shutdown 是进程级全局的，上下文、节点和 spin 线程统一由
   `ros_runtime.py` 持有；ROS 相关模块只往共享节点上挂 subscription/client，
   不得各自 `rclpy.init()` 或 `rclpy.shutdown()`。

新增数据源前，先确认有没有上面三类途径；实在没有（确需外部命令）时，先在
讨论中说明理由再实现。目前已批准的例外：`nav_tasks.py`（App 触发启停两个
固定的 `ros2 launch` 导航任务——Nav2 bringup 本质上就是拉起一堆节点进程，
无法以库的方式进程内运行）。该模块对子进程做完整管理：独立进程组、
SIGINT 优雅停止（超时 SIGKILL）、输出落日志文件只用于排查、**不解析输出
采集数据**。

## 2. Provider 模式（数据采集模块）

`network.py` / `cpu.py` / `memory.py` / `bandwidth.py` / `latency.py` / `zone_voice.py` 遵循同一套模式，新增
数据源时请照抄：

- 独立线程 + `threading.Event` 停止信号；`start()` 幂等、`stop(timeout)` 可join。
  例外：ROS 相关模块（`battery.py` 订阅、`robot_control.py` 服务调用、
  `zone_nav.py` 发布）不自带线程，`start(node)` 挂载到共享 runtime 节点上，
  由 runtime 的 spin 线程驱动。
- **优雅降级**：依赖缺失时（rclpy 不可导入、没有无线网卡、`/proc` 文件不存在）
  `enabled` 为 False，`start()` 打日志后直接返回，不影响桥接其余功能；
  对应 GATT 特征值保持 `UNKNOWN`。
- **变化才通知**：轮询类 provider 内部去重（值未变化或变化未超阈值不回调），
  避免无意义的 BLE Notify 流量。有"无瞬时值、需差分"语义的数据（如 CPU 使用率）
  首次采样只建基线。
- 重依赖（rclpy 等）**延迟导入**，保证非 ROS 环境下模块仍可 import、测试可运行。

## 3. GATT 特征值约定

- 协议是**纯文本**风格，不引入 JSON 包装；文本一律 UTF-8，通知上限 180 字节
  （用 `_bounded_text` 截断）。
- 数据未获取到时统一报 `UNKNOWN`；字段级缺失用占位符（如 `-`）而不是改变
  字段个数，保证 App 侧解析位置稳定。注意 SSID 这类可能含空格的字段必须放最后。
- 新特征值：UUID 沿用 `12345678-...-abcdefN` 递增，对象路径 `charN`/`desc0`，
  需要在 `gatt_server.py` 里同步登记四处——常量区、特征值类、
  `ControlService.Characteristics`、`GattApplication.GetManagedObjects`，
  以及 `_run()` 里的创建/CCCD/发布。
  **尾号已用尽**：`abcdef0`–`abcdeff` 均已占用（Zone Voice 为 `abcdeff` /
  `char14`）。再新增特征值必须换 UUID 方案（例如最后一组改为
  `56789abcd010`），不要把 UUID 最后一组扩成 13 个十六进制字符。
- BLE 对 App 保持纯文本；下游本机协议可以是 JSON Lines（如
  `zone_voice.py` 对接 `zone_voice_player`），翻译发生在桥接模块内部，
  不得把 JSON 包装泄漏到 GATT 特征值。
- 跨线程更新特征值必须走 `GLib.idle_add`（GATT 对象属于 GLib 事件循环线程）。

## 4. 文档同步（改代码必查）

任何改变了**外部行为**的修改，提交前检查并同步更新：

- `BLE_CONTROL_PROTOCOL.md`：特征值/格式/交互语义变化；新增章节注意后续章节
  重新编号，协议版本号递增。**该文档只写 App 可见的协议契约**（特征值、
  文本格式、交互时序、错误码、连接约束），不写机器人侧实现细节（进程/线程
  模型、共享 rclpy 节点、配置文件路径、日志文件、内部模块行为等）；需要描述
  机器人行为时只写对外可见的语义（如"停止写入后机器人会自动停车"），实现
  说明放 README.md 或本文档。
- `README.md`：架构清单、功能小节、部署/依赖说明。
- `config.yaml.example`：新增配置项必须带注释示例。
- `AGENTS.md`（本文）：新形成的规范或经验。

文档用中文；代码内的注释、docstring、日志用英文（与现有代码保持一致）。

## 5. 测试

- 改动必须跑通全部测试：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/ -q`
  （若 shell 未 source ROS2 环境，可省略环境变量。ROS 自带的 pytest 插件会与
  项目 pytest 版本冲突导致测试无法启动）。
- 测试**不依赖真实硬件/服务**：GATT 测试只构造 D-Bus 接口对象；rclpy 用
  `sys.modules` 注入假模块（见 `tests/test_battery.py`）；内核接口读取函数
  设计为模块级函数 + 可注入路径，测试用 monkeypatch 替换
  （见 `tests/test_network.py` / `tests/test_cpu.py` / `tests/test_memory.py`）。
- 新 provider 必测：解析函数（含坏输入）、变化去重逻辑、禁用路径、线程启停。

## 6. 依赖与环境

- 依赖用 **uv** 管理（`uv add <pkg>`），不要乱动系统 Python。
- venv 必须带 `--system-site-packages`（PyGObject/BlueZ 只能走系统包）；
  新增 C 扩展依赖时，在 README 说明构建要求或系统包替代方案。
- 目标 Python 为系统 3.10，语法和库选择不要超出 3.10 能力。

## 7. 通用要求

- **最小改动**：只动任务涉及的文件和行为，不顺手重构；新代码风格对齐周围
  现有代码（命名、注释密度、结构）。
- 桥接各组成部分（GATT 服务、socket 客户端、provider）保持解耦：
  provider 不知道 GATT 的存在，只通过回调上报；装配关系集中在 `main.py`。
- BLE 协议保持向后兼容意识：已有特征值的文本格式变化是破坏性变更，
  需要在协议文档中明确写出，并提醒 App 端配合调整。
