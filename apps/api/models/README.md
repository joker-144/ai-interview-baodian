# 本地模型目录（apps/api/models/）

存放**可直接在本项目内离线运行**的模型权重。该目录**整体入仓跟踪**（团队共享、
克隆即用、免重复下载）；如需重新下载或新增模型，执行下载脚本即可。

## 下载

```powershell
# 项目根目录执行；默认下载 bge-small-zh-v1.5（约 91MB，11s 左右）
python scripts/download_models.py

python scripts/download_models.py --list                # 查看清单与本地状态
python scripts/download_models.py --model bge-large-zh-v1.5
python scripts/download_models.py --source hf-mirror    # 指定下载源
```

下载源按 `modelscope → hf-mirror → huggingface` 顺序自动重试，单文件支持断点续传。
实测国内直连 `huggingface.co` 超时，且 hf-mirror 的 `model.safetensors` 会 302 到
`cas-bridge.xethub.hf.co`（同样不通），故默认优先 ModelScope CDN。

## 目录约定

```
apps/api/models/
├── README.md                 # 本文件
├── manifest.json             # 下载脚本生成：本地模型清单，管理端「本地模型」列表的数据源
└── <model_id>/               # 例：bge-small-zh-v1.5/
    ├── config.json / modules.json / 1_Pooling/config.json
    ├── tokenizer_config.json / vocab.txt / tokenizer.json
    └── model.safetensors     # 或 pytorch_model.bin，两者任一即可
```

## 运行时如何被调用

- `app/local_models.py`：懒加载 + 进程内单例，`SentenceTransformer(local_files_only=True)`
  直接从本目录读取，**不发任何公网请求**；首次加载约 5~7s，之后每次向量化为毫秒级。
- `app/routers/admin.py`：Embedding 层供应商选「本地模型（项目内置）」后，
  「测试连接」会**真跑一次向量化**并做相似度合理性校验，回传真实延迟与维度。
- 线程数由 `LOCAL_MODEL_THREADS`（默认 2）控制。受限环境（容器 / 低内存）下若按核数
  预分配，OpenBLAS 会直接 `Memory allocation still failed`，因此默认保守取值。

## 边界：哪些放本地、哪些走云端

| 分层 | 默认供应商 | 说明 |
|---|---|---|
| Embedding | **本地模型** | 高频、纯计算、无生成质量差异；每次出题要打几百次向量，本地跑边际成本为 0，且简历/JD 不出网 |
| 主模型 / 轻量模型 / 多模态 / 语音 | 云端供应商 | 生成质量依赖大模型，本地不承接；供应商与模型名在管理端 `/admin/models` 配置 |

## 新增一个本地模型

1. 在 `scripts/download_models.py` 的 `MODEL_REGISTRY` 加一条（`label / kind / dimensions / repos`）；
2. 执行 `python scripts/download_models.py --model <model_id>`；
3. 管理端刷新即可在下拉中看到——`manifest.json` 是唯一数据源，前后端无需改代码。
