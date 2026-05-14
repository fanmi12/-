# wxauto-batch-sender

基于 [wxauto](https://github.com/cluic/wxauto) 的微信批量消息发送 GUI 工具。

## 功能特性

- **批量发送消息** - 从 Excel/CSV 导入联系人列表，批量发送文本和图片消息
- **智能姓名提取** - 自动从备注名中提取纯姓名，支持称谓去除、年级过滤、复姓识别
- **过滤规则管理** - 可视化配置过滤关键词、清洗规则、无效名称判定等
- **防风控机制** - 可调节发送间隔、每日上限、随机延迟等安全设置
- **AI 文案改写** - 支持接入 Kimi API 进行消息模板智能改写，降低重复率
- **发送结果导出** - 支持导出发送成功/失败/被删联系人列表（TXT/Excel）
- **联系人备注修改** - 内置备注批量修改工具
- **标签筛选** - 支持按微信标签筛选联系人
- **字体缩放** - 界面支持 Ctrl+滚轮缩放字体

## 环境要求

- Windows 10/11
- Python 3.10+
- 微信 PC 版 3.9.2.23（wxauto 当前仅支持此版本）具体可以参考https://github.com/Skyler1n/WeChat3.9-32bit-Compatibility-Launcher
- 微信已登录且窗口可见

## 安装

```bash
# 克隆项目
git clone https://github.com/your-username/wxauto-batch-sender.git
cd wxauto-batch-sender

# 安装依赖
pip install -r requirements.txt
```

## 使用

```bash
python main.py
```

1. 启动后确保微信窗口已打开并登录
2. 点击「获取联系人」加载微信联系人列表
3. 可选：导入 Excel/CSV 联系人文件
4. 配置消息模板（支持 `{name}`、`{emoji}` 占位符）
5. 选择要发送的联系人
6. 点击「开始发送」

## 配置说明

配置文件位于 `config/app_config.json`，主要配置项：

| 配置项 | 说明 |
|--------|------|
| `message_template` | 消息模板，支持 `{name}`、`{emoji}` 占位符 |
| `filter_keywords` | 过滤关键词列表，包含这些词的联系人将被过滤 |
| `clean_keywords` | 清洗关键词，发送时去除名字中的这些词 |
| `title_keywords` | 称谓关键词，去除名字中的称谓（如妈妈、爸爸） |
| `grade_keywords` | 年级关键词，去除名字中的年级信息 |
| `noise_keywords` | 干扰词库，去除名字中的公司/机构等干扰词 |
| `compound_surnames` | 复姓库，用于正确识别复姓 |
| `delete_keywords` | 被删/拉黑检测关键词 |
| `invalid_keywords` | 无效名称关键词 |
| `anti_risk_mode` | 防风控模式开关 |
| `daily_limit` | 每日发送上限 |
| `kimi_rewrite_enabled` | 是否启用 AI 文案改写 |

## 项目结构

```
wxauto-batch-sender/
├── main.py              # 入口文件
├── core/
│   ├── __init__.py
│   ├── app.py           # 主 GUI 界面和业务逻辑
│   ├── base.py          # 基类和通用组件
│   └── remark_modifier.py  # 备注修改工具
├── config/
│   └── app_config.json  # 应用配置
├── requirements.txt
├── pyproject.toml
└── README.md
```

## 许可证

MIT License
