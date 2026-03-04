# 🎙️ 智能语音评测与 AI 辅导系统

基于 MindSpore、华为云 SIS 与 DeepSeek 大模型构建的多租户教育 SaaS 平台。

## ✨ 核心功能
1. **双轨制语音评测**：结合 TF-IDF 特征向量与 Difflib 算法，实现精准纠偏。
2. **AI 智能出题**：接入大模型，一键生成符合中小学课标的语、数、英题库。
3. **沉浸式讲台模式**：全自动语音寻人、身份确认与循环测验。
4. **多租户架构**：班级码隔离，数据互不干扰。

## 🚀 部署指南
1. 安装依赖：`pip install flask flask-cors pydub requests mindspore scikit-learn jieba websocket-client huaweicloudsdksis`
2. 填写密钥：请在 `app.py` 和 `sis_evaluator.py` 中填入您的硅基流动 API Key 及华为云 AK/SK。
3. 启动服务：`gunicorn -w 4 -b 0.0.0.0:5000 app:app`
