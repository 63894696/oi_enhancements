# -*- coding: utf-8 -*-
"""Prisir Fcontent — 内容搜索独立可选模块(探囊外挂层)。

按内容找文件(docx/pdf/pptx/txt/md/代码等),与探囊 findex(只存元数据)解耦:
独立 SQLite FTS5 库、独立 enable/disable、逐目录显式授权。

红线(对齐 docs/prisir-findex-content-search-boundary-2026-08-22.md):
- 默认关,显式 enable 且必须给 roots(逐目录授权,不做全盘)。
- 内容只进本机 FTS5 库,不上云、不出本机;disable 清空。
- FTS5 只存分词结果(content_tok),不存原始全文(不可逆回原文,最小读取)。
- OCR(图片文字)是独立可选能力:需装 rapidocr_onnxruntime + enable(ocr=True)
  显式开启才把图片纳入索引;默认关。置信门控 score<0.8 的行不入库。

用法:
  from prisir_fcontent import Fcontent
  fc = Fcontent()                              # 开/建库(不扫盘)
  fc.enable([r"C:\\Users\\me\\docs"])          # 显式授权目录,首扫建索引(不含图片)
  fc.enable([r"C:\\Users\\me\\docs"], ocr=True)  # 同时开启图片 OCR(需已装 rapidocr)
  fc.search("季度报告")                        # 内容子串搜索,带匹配片段
  fc.status()                                  # {enabled, indexed_count, ocr:{available...}}
  fc.disable()                                 # 清空内容索引
"""
from .engine import Fcontent

__all__ = ["Fcontent"]
