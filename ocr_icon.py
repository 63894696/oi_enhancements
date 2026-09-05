import sys
from rapidocr_onnxruntime import RapidOCR
ocr = RapidOCR()
result, elapse = ocr(r'C:/Users/Administrator/oi_enhancements/prisiragent-shell/icon.png')
with open(r'C:/Users/Administrator/oi_enhancements/ocr_out.txt', 'w', encoding='utf-8') as f:
    f.write('RESULT: %r\nELAPSE: %r\n' % (result, elapse))
