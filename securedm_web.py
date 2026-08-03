"""securedm_web.py — Agent-First OS 阶段 3.A SecureDM 自研 1:1 IM 端(L4)

架构 §3.A:SimpleX 协议自有前端,1:1 E2E 加密私信。复用 simplex_runtime/tools/integrity,
做成"联系人列表 + 会话窗"的 IM 形态,交互仍只有对话/确认(架构 §7.4)。

与 l4_web 的关系:l4_web 是"人↔agent 对话反馈窗"(走 daemon 工具循环);SecureDM 是
"人↔人 / 人↔agent 的 1:1 私信端",直接驱动 simplex_runtime(不经 daemon ask)。

核心功能:
  - 联系人列表(simplex_list_contacts)+ 新建邀请(simplex_create_invitation 生成链接给人)
  - 会话窗:读消息(chat_texts 持久历史)+ 发消息(simplex_send_message)
  - 文件消息:列出待下载文件(simplex_list_incoming_files)+ 下载(simplex_receive_file)
  - **签名验证标**:收到的文件若带有效签名清单,显示"✓ 已验证来自 X"(simplex_verify_file_by_manifest);
    验证失败醒目提示。发送方可选 simplex_send_file_signed 发签名文件。
  - token 认证(与 l4_web 同一信任根文件)。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simplex_tools as st  # noqa: E402
import simplex_files as sf  # noqa: E402
import simplex_integrity as si  # noqa: E402
import simplex_a2h as sa  # noqa: E402
from simplex_runtime import SimplexRuntime  # noqa: E402

DM_HOST = os.environ.get("DM_HOST", "127.0.0.1")
DM_PORT = int(os.environ.get("DM_PORT", "18801"))
# 实例身份(多实例部署时区分):DM_IDENTITY=显示名,DM_DB=该实例独立的 simplex 数据目录前缀
DM_IDENTITY = os.environ.get("DM_IDENTITY", "oiagent")
DM_DB_PREFIX = os.environ.get("DM_DB_PREFIX", "")
_TOKEN_FILE = Path.home() / ".local" / "share" / "aureon" / "l4_token"


def _token() -> str:
    try:
        if _TOKEN_FILE.exists():
            return _TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("L4_TOKEN", "")


_ACCESS_TOKEN = _token()


def _ok(o, **e):
    return {"ok": True, "output": o, **e}


# ────────────────────────────────────────────────────────────────────── #
# QR Code Generator for JavaScript — 内联进 _PAGE 的离线 QR 库
# 来源: Kazuhiko Arase <qrcode-generator> v1.4.4 (单文件零依赖)
#   https://github.com/kazuhikoarase/qrcode-generator (js/qrcode.js)
# License: MIT (http://www.opensource.org/licenses/mit-license.php)
# Copyright (c) 2009 Kazuhiko Arase。'QR Code' 为 DENSO WAVE 注册商标。
# 全文内联,无任何外部 script/img/fetch,符合 SecureDM 离线匿名约束。
# ────────────────────────────────────────────────────────────────────── #
_QRCODE_LIB = r"""
//---------------------------------------------------------------------
//
// QR Code Generator for JavaScript
//
// Copyright (c) 2009 Kazuhiko Arase
//
// URL: http://www.d-project.com/
//
// Licensed under the MIT license:
//  http://www.opensource.org/licenses/mit-license.php
//
// The word 'QR Code' is registered trademark of
// DENSO WAVE INCORPORATED
//  http://www.denso-wave.com/qrcode/faqpatent-e.html
//
//---------------------------------------------------------------------

var qrcode = function() {

  //---------------------------------------------------------------------
  // qrcode
  //---------------------------------------------------------------------

  /**
   * qrcode
   * @param typeNumber 1 to 40
   * @param errorCorrectionLevel 'L','M','Q','H'
   */
  var qrcode = function(typeNumber, errorCorrectionLevel) {

    var PAD0 = 0xEC;
    var PAD1 = 0x11;

    var _typeNumber = typeNumber;
    var _errorCorrectionLevel = QRErrorCorrectionLevel[errorCorrectionLevel];
    var _modules = null;
    var _moduleCount = 0;
    var _dataCache = null;
    var _dataList = [];

    var _this = {};

    var makeImpl = function(test, maskPattern) {

      _moduleCount = _typeNumber * 4 + 17;
      _modules = function(moduleCount) {
        var modules = new Array(moduleCount);
        for (var row = 0; row < moduleCount; row += 1) {
          modules[row] = new Array(moduleCount);
          for (var col = 0; col < moduleCount; col += 1) {
            modules[row][col] = null;
          }
        }
        return modules;
      }(_moduleCount);

      setupPositionProbePattern(0, 0);
      setupPositionProbePattern(_moduleCount - 7, 0);
      setupPositionProbePattern(0, _moduleCount - 7);
      setupPositionAdjustPattern();
      setupTimingPattern();
      setupTypeInfo(test, maskPattern);

      if (_typeNumber >= 7) {
        setupTypeNumber(test);
      }

      if (_dataCache == null) {
        _dataCache = createData(_typeNumber, _errorCorrectionLevel, _dataList);
      }

      mapData(_dataCache, maskPattern);
    };

    var setupPositionProbePattern = function(row, col) {

      for (var r = -1; r <= 7; r += 1) {

        if (row + r <= -1 || _moduleCount <= row + r) continue;

        for (var c = -1; c <= 7; c += 1) {

          if (col + c <= -1 || _moduleCount <= col + c) continue;

          if ( (0 <= r && r <= 6 && (c == 0 || c == 6) )
              || (0 <= c && c <= 6 && (r == 0 || r == 6) )
              || (2 <= r && r <= 4 && 2 <= c && c <= 4) ) {
            _modules[row + r][col + c] = true;
          } else {
            _modules[row + r][col + c] = false;
          }
        }
      }
    };

    var getBestMaskPattern = function() {

      var minLostPoint = 0;
      var pattern = 0;

      for (var i = 0; i < 8; i += 1) {

        makeImpl(true, i);

        var lostPoint = QRUtil.getLostPoint(_this);

        if (i == 0 || minLostPoint > lostPoint) {
          minLostPoint = lostPoint;
          pattern = i;
        }
      }

      return pattern;
    };

    var setupTimingPattern = function() {

      for (var r = 8; r < _moduleCount - 8; r += 1) {
        if (_modules[r][6] != null) {
          continue;
        }
        _modules[r][6] = (r % 2 == 0);
      }

      for (var c = 8; c < _moduleCount - 8; c += 1) {
        if (_modules[6][c] != null) {
          continue;
        }
        _modules[6][c] = (c % 2 == 0);
      }
    };

    var setupPositionAdjustPattern = function() {

      var pos = QRUtil.getPatternPosition(_typeNumber);

      for (var i = 0; i < pos.length; i += 1) {

        for (var j = 0; j < pos.length; j += 1) {

          var row = pos[i];
          var col = pos[j];

          if (_modules[row][col] != null) {
            continue;
          }

          for (var r = -2; r <= 2; r += 1) {

            for (var c = -2; c <= 2; c += 1) {

              if (r == -2 || r == 2 || c == -2 || c == 2
                  || (r == 0 && c == 0) ) {
                _modules[row + r][col + c] = true;
              } else {
                _modules[row + r][col + c] = false;
              }
            }
          }
        }
      }
    };

    var setupTypeNumber = function(test) {

      var bits = QRUtil.getBCHTypeNumber(_typeNumber);

      for (var i = 0; i < 18; i += 1) {
        var mod = (!test && ( (bits >> i) & 1) == 1);
        _modules[Math.floor(i / 3)][i % 3 + _moduleCount - 8 - 3] = mod;
      }

      for (var i = 0; i < 18; i += 1) {
        var mod = (!test && ( (bits >> i) & 1) == 1);
        _modules[i % 3 + _moduleCount - 8 - 3][Math.floor(i / 3)] = mod;
      }
    };

    var setupTypeInfo = function(test, maskPattern) {

      var data = (_errorCorrectionLevel << 3) | maskPattern;
      var bits = QRUtil.getBCHTypeInfo(data);

      // vertical
      for (var i = 0; i < 15; i += 1) {

        var mod = (!test && ( (bits >> i) & 1) == 1);

        if (i < 6) {
          _modules[i][8] = mod;
        } else if (i < 8) {
          _modules[i + 1][8] = mod;
        } else {
          _modules[_moduleCount - 15 + i][8] = mod;
        }
      }

      // horizontal
      for (var i = 0; i < 15; i += 1) {

        var mod = (!test && ( (bits >> i) & 1) == 1);

        if (i < 8) {
          _modules[8][_moduleCount - i - 1] = mod;
        } else if (i < 9) {
          _modules[8][15 - i - 1 + 1] = mod;
        } else {
          _modules[8][15 - i - 1] = mod;
        }
      }

      // fixed module
      _modules[_moduleCount - 8][8] = (!test);
    };

    var mapData = function(data, maskPattern) {

      var inc = -1;
      var row = _moduleCount - 1;
      var bitIndex = 7;
      var byteIndex = 0;
      var maskFunc = QRUtil.getMaskFunction(maskPattern);

      for (var col = _moduleCount - 1; col > 0; col -= 2) {

        if (col == 6) col -= 1;

        while (true) {

          for (var c = 0; c < 2; c += 1) {

            if (_modules[row][col - c] == null) {

              var dark = false;

              if (byteIndex < data.length) {
                dark = ( ( (data[byteIndex] >>> bitIndex) & 1) == 1);
              }

              var mask = maskFunc(row, col - c);

              if (mask) {
                dark = !dark;
              }

              _modules[row][col - c] = dark;
              bitIndex -= 1;

              if (bitIndex == -1) {
                byteIndex += 1;
                bitIndex = 7;
              }
            }
          }

          row += inc;

          if (row < 0 || _moduleCount <= row) {
            row -= inc;
            inc = -inc;
            break;
          }
        }
      }
    };

    var createBytes = function(buffer, rsBlocks) {

      var offset = 0;

      var maxDcCount = 0;
      var maxEcCount = 0;

      var dcdata = new Array(rsBlocks.length);
      var ecdata = new Array(rsBlocks.length);

      for (var r = 0; r < rsBlocks.length; r += 1) {

        var dcCount = rsBlocks[r].dataCount;
        var ecCount = rsBlocks[r].totalCount - dcCount;

        maxDcCount = Math.max(maxDcCount, dcCount);
        maxEcCount = Math.max(maxEcCount, ecCount);

        dcdata[r] = new Array(dcCount);

        for (var i = 0; i < dcdata[r].length; i += 1) {
          dcdata[r][i] = 0xff & buffer.getBuffer()[i + offset];
        }
        offset += dcCount;

        var rsPoly = QRUtil.getErrorCorrectPolynomial(ecCount);
        var rawPoly = qrPolynomial(dcdata[r], rsPoly.getLength() - 1);

        var modPoly = rawPoly.mod(rsPoly);
        ecdata[r] = new Array(rsPoly.getLength() - 1);
        for (var i = 0; i < ecdata[r].length; i += 1) {
          var modIndex = i + modPoly.getLength() - ecdata[r].length;
          ecdata[r][i] = (modIndex >= 0)? modPoly.getAt(modIndex) : 0;
        }
      }

      var totalCodeCount = 0;
      for (var i = 0; i < rsBlocks.length; i += 1) {
        totalCodeCount += rsBlocks[i].totalCount;
      }

      var data = new Array(totalCodeCount);
      var index = 0;

      for (var i = 0; i < maxDcCount; i += 1) {
        for (var r = 0; r < rsBlocks.length; r += 1) {
          if (i < dcdata[r].length) {
            data[index] = dcdata[r][i];
            index += 1;
          }
        }
      }

      for (var i = 0; i < maxEcCount; i += 1) {
        for (var r = 0; r < rsBlocks.length; r += 1) {
          if (i < ecdata[r].length) {
            data[index] = ecdata[r][i];
            index += 1;
          }
        }
      }

      return data;
    };

    var createData = function(typeNumber, errorCorrectionLevel, dataList) {

      var rsBlocks = QRRSBlock.getRSBlocks(typeNumber, errorCorrectionLevel);

      var buffer = qrBitBuffer();

      for (var i = 0; i < dataList.length; i += 1) {
        var data = dataList[i];
        buffer.put(data.getMode(), 4);
        buffer.put(data.getLength(), QRUtil.getLengthInBits(data.getMode(), typeNumber) );
        data.write(buffer);
      }

      // calc num max data.
      var totalDataCount = 0;
      for (var i = 0; i < rsBlocks.length; i += 1) {
        totalDataCount += rsBlocks[i].dataCount;
      }

      if (buffer.getLengthInBits() > totalDataCount * 8) {
        throw 'code length overflow. ('
          + buffer.getLengthInBits()
          + '>'
          + totalDataCount * 8
          + ')';
      }

      // end code
      if (buffer.getLengthInBits() + 4 <= totalDataCount * 8) {
        buffer.put(0, 4);
      }

      // padding
      while (buffer.getLengthInBits() % 8 != 0) {
        buffer.putBit(false);
      }

      // padding
      while (true) {

        if (buffer.getLengthInBits() >= totalDataCount * 8) {
          break;
        }
        buffer.put(PAD0, 8);

        if (buffer.getLengthInBits() >= totalDataCount * 8) {
          break;
        }
        buffer.put(PAD1, 8);
      }

      return createBytes(buffer, rsBlocks);
    };

    _this.addData = function(data, mode) {

      mode = mode || 'Byte';

      var newData = null;

      switch(mode) {
      case 'Numeric' :
        newData = qrNumber(data);
        break;
      case 'Alphanumeric' :
        newData = qrAlphaNum(data);
        break;
      case 'Byte' :
        newData = qr8BitByte(data);
        break;
      case 'Kanji' :
        newData = qrKanji(data);
        break;
      default :
        throw 'mode:' + mode;
      }

      _dataList.push(newData);
      _dataCache = null;
    };

    _this.isDark = function(row, col) {
      if (row < 0 || _moduleCount <= row || col < 0 || _moduleCount <= col) {
        throw row + ',' + col;
      }
      return _modules[row][col];
    };

    _this.getModuleCount = function() {
      return _moduleCount;
    };

    _this.make = function() {
      if (_typeNumber < 1) {
        var typeNumber = 1;

        for (; typeNumber < 40; typeNumber++) {
          var rsBlocks = QRRSBlock.getRSBlocks(typeNumber, _errorCorrectionLevel);
          var buffer = qrBitBuffer();

          for (var i = 0; i < _dataList.length; i++) {
            var data = _dataList[i];
            buffer.put(data.getMode(), 4);
            buffer.put(data.getLength(), QRUtil.getLengthInBits(data.getMode(), typeNumber) );
            data.write(buffer);
          }

          var totalDataCount = 0;
          for (var i = 0; i < rsBlocks.length; i++) {
            totalDataCount += rsBlocks[i].dataCount;
          }

          if (buffer.getLengthInBits() <= totalDataCount * 8) {
            break;
          }
        }

        _typeNumber = typeNumber;
      }

      makeImpl(false, getBestMaskPattern() );
    };

    _this.createTableTag = function(cellSize, margin) {

      cellSize = cellSize || 2;
      margin = (typeof margin == 'undefined')? cellSize * 4 : margin;

      var qrHtml = '';

      qrHtml += '<table style="';
      qrHtml += ' border-width: 0px; border-style: none;';
      qrHtml += ' border-collapse: collapse;';
      qrHtml += ' padding: 0px; margin: ' + margin + 'px;';
      qrHtml += '">';
      qrHtml += '<tbody>';

      for (var r = 0; r < _this.getModuleCount(); r += 1) {

        qrHtml += '<tr>';

        for (var c = 0; c < _this.getModuleCount(); c += 1) {
          qrHtml += '<td style="';
          qrHtml += ' border-width: 0px; border-style: none;';
          qrHtml += ' border-collapse: collapse;';
          qrHtml += ' padding: 0px; margin: 0px;';
          qrHtml += ' width: ' + cellSize + 'px;';
          qrHtml += ' height: ' + cellSize + 'px;';
          qrHtml += ' background-color: ';
          qrHtml += _this.isDark(r, c)? '#000000' : '#ffffff';
          qrHtml += ';';
          qrHtml += '"/>';
        }

        qrHtml += '</tr>';
      }

      qrHtml += '</tbody>';
      qrHtml += '</table>';

      return qrHtml;
    };

    _this.createSvgTag = function(cellSize, margin, alt, title) {

      var opts = {};
      if (typeof arguments[0] == 'object') {
        // Called by options.
        opts = arguments[0];
        // overwrite cellSize and margin.
        cellSize = opts.cellSize;
        margin = opts.margin;
        alt = opts.alt;
        title = opts.title;
      }

      cellSize = cellSize || 2;
      margin = (typeof margin == 'undefined')? cellSize * 4 : margin;

      // Compose alt property surrogate
      alt = (typeof alt === 'string') ? {text: alt} : alt || {};
      alt.text = alt.text || null;
      alt.id = (alt.text) ? alt.id || 'qrcode-description' : null;

      // Compose title property surrogate
      title = (typeof title === 'string') ? {text: title} : title || {};
      title.text = title.text || null;
      title.id = (title.text) ? title.id || 'qrcode-title' : null;

      var size = _this.getModuleCount() * cellSize + margin * 2;
      var c, mc, r, mr, qrSvg='', rect;

      rect = 'l' + cellSize + ',0 0,' + cellSize +
        ' -' + cellSize + ',0 0,-' + cellSize + 'z ';

      qrSvg += '<svg version="1.1" xmlns="http://www.w3.org/2000/svg"';
      qrSvg += !opts.scalable ? ' width="' + size + 'px" height="' + size + 'px"' : '';
      qrSvg += ' viewBox="0 0 ' + size + ' ' + size + '" ';
      qrSvg += ' preserveAspectRatio="xMinYMin meet"';
      qrSvg += (title.text || alt.text) ? ' role="img" aria-labelledby="' +
          escapeXml([title.id, alt.id].join(' ').trim() ) + '"' : '';
      qrSvg += '>';
      qrSvg += (title.text) ? '<title id="' + escapeXml(title.id) + '">' +
          escapeXml(title.text) + '</title>' : '';
      qrSvg += (alt.text) ? '<description id="' + escapeXml(alt.id) + '">' +
          escapeXml(alt.text) + '</description>' : '';
      qrSvg += '<rect width="100%" height="100%" fill="white" cx="0" cy="0"/>';
      qrSvg += '<path d="';

      for (r = 0; r < _this.getModuleCount(); r += 1) {
        mr = r * cellSize + margin;
        for (c = 0; c < _this.getModuleCount(); c += 1) {
          if (_this.isDark(r, c) ) {
            mc = c*cellSize+margin;
            qrSvg += 'M' + mc + ',' + mr + rect;
          }
        }
      }

      qrSvg += '" stroke="transparent" fill="black"/>';
      qrSvg += '</svg>';

      return qrSvg;
    };

    _this.createDataURL = function(cellSize, margin) {

      cellSize = cellSize || 2;
      margin = (typeof margin == 'undefined')? cellSize * 4 : margin;

      var size = _this.getModuleCount() * cellSize + margin * 2;
      var min = margin;
      var max = size - margin;

      return createDataURL(size, size, function(x, y) {
        if (min <= x && x < max && min <= y && y < max) {
          var c = Math.floor( (x - min) / cellSize);
          var r = Math.floor( (y - min) / cellSize);
          return _this.isDark(r, c)? 0 : 1;
        } else {
          return 1;
        }
      } );
    };

    _this.createImgTag = function(cellSize, margin, alt) {

      cellSize = cellSize || 2;
      margin = (typeof margin == 'undefined')? cellSize * 4 : margin;

      var size = _this.getModuleCount() * cellSize + margin * 2;

      var img = '';
      img += '<img';
      img += '\u0020src="';
      img += _this.createDataURL(cellSize, margin);
      img += '"';
      img += '\u0020width="';
      img += size;
      img += '"';
      img += '\u0020height="';
      img += size;
      img += '"';
      if (alt) {
        img += '\u0020alt="';
        img += escapeXml(alt);
        img += '"';
      }
      img += '/>';

      return img;
    };

    var escapeXml = function(s) {
      var escaped = '';
      for (var i = 0; i < s.length; i += 1) {
        var c = s.charAt(i);
        switch(c) {
        case '<': escaped += '&lt;'; break;
        case '>': escaped += '&gt;'; break;
        case '&': escaped += '&amp;'; break;
        case '"': escaped += '&quot;'; break;
        default : escaped += c; break;
        }
      }
      return escaped;
    };

    var _createHalfASCII = function(margin) {
      var cellSize = 1;
      margin = (typeof margin == 'undefined')? cellSize * 2 : margin;

      var size = _this.getModuleCount() * cellSize + margin * 2;
      var min = margin;
      var max = size - margin;

      var y, x, r1, r2, p;

      var blocks = {
        '██': '█',
        '█ ': '▀',
        ' █': '▄',
        '  ': ' '
      };

      var blocksLastLineNoMargin = {
        '██': '▀',
        '█ ': '▀',
        ' █': ' ',
        '  ': ' '
      };

      var ascii = '';
      for (y = 0; y < size; y += 2) {
        r1 = Math.floor((y - min) / cellSize);
        r2 = Math.floor((y + 1 - min) / cellSize);
        for (x = 0; x < size; x += 1) {
          p = '█';

          if (min <= x && x < max && min <= y && y < max && _this.isDark(r1, Math.floor((x - min) / cellSize))) {
            p = ' ';
          }

          if (min <= x && x < max && min <= y+1 && y+1 < max && _this.isDark(r2, Math.floor((x - min) / cellSize))) {
            p += ' ';
          }
          else {
            p += '█';
          }

          // Output 2 characters per pixel, to create full square. 1 character per pixels gives only half width of square.
          ascii += (margin < 1 && y+1 >= max) ? blocksLastLineNoMargin[p] : blocks[p];
        }

        ascii += '\n';
      }

      if (size % 2 && margin > 0) {
        return ascii.substring(0, ascii.length - size - 1) + Array(size+1).join('▀');
      }

      return ascii.substring(0, ascii.length-1);
    };

    _this.createASCII = function(cellSize, margin) {
      cellSize = cellSize || 1;

      if (cellSize < 2) {
        return _createHalfASCII(margin);
      }

      cellSize -= 1;
      margin = (typeof margin == 'undefined')? cellSize * 2 : margin;

      var size = _this.getModuleCount() * cellSize + margin * 2;
      var min = margin;
      var max = size - margin;

      var y, x, r, p;

      var white = Array(cellSize+1).join('██');
      var black = Array(cellSize+1).join('  ');

      var ascii = '';
      var line = '';
      for (y = 0; y < size; y += 1) {
        r = Math.floor( (y - min) / cellSize);
        line = '';
        for (x = 0; x < size; x += 1) {
          p = 1;

          if (min <= x && x < max && min <= y && y < max && _this.isDark(r, Math.floor((x - min) / cellSize))) {
            p = 0;
          }

          // Output 2 characters per pixel, to create full square. 1 character per pixels gives only half width of square.
          line += p ? white : black;
        }

        for (r = 0; r < cellSize; r += 1) {
          ascii += line + '\n';
        }
      }

      return ascii.substring(0, ascii.length-1);
    };

    _this.renderTo2dContext = function(context, cellSize) {
      cellSize = cellSize || 2;
      var length = _this.getModuleCount();
      for (var row = 0; row < length; row++) {
        for (var col = 0; col < length; col++) {
          context.fillStyle = _this.isDark(row, col) ? 'black' : 'white';
          context.fillRect(row * cellSize, col * cellSize, cellSize, cellSize);
        }
      }
    }

    return _this;
  };

  //---------------------------------------------------------------------
  // qrcode.stringToBytes
  //---------------------------------------------------------------------

  qrcode.stringToBytesFuncs = {
    'default' : function(s) {
      var bytes = [];
      for (var i = 0; i < s.length; i += 1) {
        var c = s.charCodeAt(i);
        bytes.push(c & 0xff);
      }
      return bytes;
    }
  };

  qrcode.stringToBytes = qrcode.stringToBytesFuncs['default'];

  //---------------------------------------------------------------------
  // qrcode.createStringToBytes
  //---------------------------------------------------------------------

  /**
   * @param unicodeData base64 string of byte array.
   * [16bit Unicode],[16bit Bytes], ...
   * @param numChars
   */
  qrcode.createStringToBytes = function(unicodeData, numChars) {

    // create conversion map.

    var unicodeMap = function() {

      var bin = base64DecodeInputStream(unicodeData);
      var read = function() {
        var b = bin.read();
        if (b == -1) throw 'eof';
        return b;
      };

      var count = 0;
      var unicodeMap = {};
      while (true) {
        var b0 = bin.read();
        if (b0 == -1) break;
        var b1 = read();
        var b2 = read();
        var b3 = read();
        var k = String.fromCharCode( (b0 << 8) | b1);
        var v = (b2 << 8) | b3;
        unicodeMap[k] = v;
        count += 1;
      }
      if (count != numChars) {
        throw count + ' != ' + numChars;
      }

      return unicodeMap;
    }();

    var unknownChar = '?'.charCodeAt(0);

    return function(s) {
      var bytes = [];
      for (var i = 0; i < s.length; i += 1) {
        var c = s.charCodeAt(i);
        if (c < 128) {
          bytes.push(c);
        } else {
          var b = unicodeMap[s.charAt(i)];
          if (typeof b == 'number') {
            if ( (b & 0xff) == b) {
              // 1byte
              bytes.push(b);
            } else {
              // 2bytes
              bytes.push(b >>> 8);
              bytes.push(b & 0xff);
            }
          } else {
            bytes.push(unknownChar);
          }
        }
      }
      return bytes;
    };
  };

  //---------------------------------------------------------------------
  // QRMode
  //---------------------------------------------------------------------

  var QRMode = {
    MODE_NUMBER :    1 << 0,
    MODE_ALPHA_NUM : 1 << 1,
    MODE_8BIT_BYTE : 1 << 2,
    MODE_KANJI :     1 << 3
  };

  //---------------------------------------------------------------------
  // QRErrorCorrectionLevel
  //---------------------------------------------------------------------

  var QRErrorCorrectionLevel = {
    L : 1,
    M : 0,
    Q : 3,
    H : 2
  };

  //---------------------------------------------------------------------
  // QRMaskPattern
  //---------------------------------------------------------------------

  var QRMaskPattern = {
    PATTERN000 : 0,
    PATTERN001 : 1,
    PATTERN010 : 2,
    PATTERN011 : 3,
    PATTERN100 : 4,
    PATTERN101 : 5,
    PATTERN110 : 6,
    PATTERN111 : 7
  };

  //---------------------------------------------------------------------
  // QRUtil
  //---------------------------------------------------------------------

  var QRUtil = function() {

    var PATTERN_POSITION_TABLE = [
      [],
      [6, 18],
      [6, 22],
      [6, 26],
      [6, 30],
      [6, 34],
      [6, 22, 38],
      [6, 24, 42],
      [6, 26, 46],
      [6, 28, 50],
      [6, 30, 54],
      [6, 32, 58],
      [6, 34, 62],
      [6, 26, 46, 66],
      [6, 26, 48, 70],
      [6, 26, 50, 74],
      [6, 30, 54, 78],
      [6, 30, 56, 82],
      [6, 30, 58, 86],
      [6, 34, 62, 90],
      [6, 28, 50, 72, 94],
      [6, 26, 50, 74, 98],
      [6, 30, 54, 78, 102],
      [6, 28, 54, 80, 106],
      [6, 32, 58, 84, 110],
      [6, 30, 58, 86, 114],
      [6, 34, 62, 90, 118],
      [6, 26, 50, 74, 98, 122],
      [6, 30, 54, 78, 102, 126],
      [6, 26, 52, 78, 104, 130],
      [6, 30, 56, 82, 108, 134],
      [6, 34, 60, 86, 112, 138],
      [6, 30, 58, 86, 114, 142],
      [6, 34, 62, 90, 118, 146],
      [6, 30, 54, 78, 102, 126, 150],
      [6, 24, 50, 76, 102, 128, 154],
      [6, 28, 54, 80, 106, 132, 158],
      [6, 32, 58, 84, 110, 136, 162],
      [6, 26, 54, 82, 110, 138, 166],
      [6, 30, 58, 86, 114, 142, 170]
    ];
    var G15 = (1 << 10) | (1 << 8) | (1 << 5) | (1 << 4) | (1 << 2) | (1 << 1) | (1 << 0);
    var G18 = (1 << 12) | (1 << 11) | (1 << 10) | (1 << 9) | (1 << 8) | (1 << 5) | (1 << 2) | (1 << 0);
    var G15_MASK = (1 << 14) | (1 << 12) | (1 << 10) | (1 << 4) | (1 << 1);

    var _this = {};

    var getBCHDigit = function(data) {
      var digit = 0;
      while (data != 0) {
        digit += 1;
        data >>>= 1;
      }
      return digit;
    };

    _this.getBCHTypeInfo = function(data) {
      var d = data << 10;
      while (getBCHDigit(d) - getBCHDigit(G15) >= 0) {
        d ^= (G15 << (getBCHDigit(d) - getBCHDigit(G15) ) );
      }
      return ( (data << 10) | d) ^ G15_MASK;
    };

    _this.getBCHTypeNumber = function(data) {
      var d = data << 12;
      while (getBCHDigit(d) - getBCHDigit(G18) >= 0) {
        d ^= (G18 << (getBCHDigit(d) - getBCHDigit(G18) ) );
      }
      return (data << 12) | d;
    };

    _this.getPatternPosition = function(typeNumber) {
      return PATTERN_POSITION_TABLE[typeNumber - 1];
    };

    _this.getMaskFunction = function(maskPattern) {

      switch (maskPattern) {

      case QRMaskPattern.PATTERN000 :
        return function(i, j) { return (i + j) % 2 == 0; };
      case QRMaskPattern.PATTERN001 :
        return function(i, j) { return i % 2 == 0; };
      case QRMaskPattern.PATTERN010 :
        return function(i, j) { return j % 3 == 0; };
      case QRMaskPattern.PATTERN011 :
        return function(i, j) { return (i + j) % 3 == 0; };
      case QRMaskPattern.PATTERN100 :
        return function(i, j) { return (Math.floor(i / 2) + Math.floor(j / 3) ) % 2 == 0; };
      case QRMaskPattern.PATTERN101 :
        return function(i, j) { return (i * j) % 2 + (i * j) % 3 == 0; };
      case QRMaskPattern.PATTERN110 :
        return function(i, j) { return ( (i * j) % 2 + (i * j) % 3) % 2 == 0; };
      case QRMaskPattern.PATTERN111 :
        return function(i, j) { return ( (i * j) % 3 + (i + j) % 2) % 2 == 0; };

      default :
        throw 'bad maskPattern:' + maskPattern;
      }
    };

    _this.getErrorCorrectPolynomial = function(errorCorrectLength) {
      var a = qrPolynomial([1], 0);
      for (var i = 0; i < errorCorrectLength; i += 1) {
        a = a.multiply(qrPolynomial([1, QRMath.gexp(i)], 0) );
      }
      return a;
    };

    _this.getLengthInBits = function(mode, type) {

      if (1 <= type && type < 10) {

        // 1 - 9

        switch(mode) {
        case QRMode.MODE_NUMBER    : return 10;
        case QRMode.MODE_ALPHA_NUM : return 9;
        case QRMode.MODE_8BIT_BYTE : return 8;
        case QRMode.MODE_KANJI     : return 8;
        default :
          throw 'mode:' + mode;
        }

      } else if (type < 27) {

        // 10 - 26

        switch(mode) {
        case QRMode.MODE_NUMBER    : return 12;
        case QRMode.MODE_ALPHA_NUM : return 11;
        case QRMode.MODE_8BIT_BYTE : return 16;
        case QRMode.MODE_KANJI     : return 10;
        default :
          throw 'mode:' + mode;
        }

      } else if (type < 41) {

        // 27 - 40

        switch(mode) {
        case QRMode.MODE_NUMBER    : return 14;
        case QRMode.MODE_ALPHA_NUM : return 13;
        case QRMode.MODE_8BIT_BYTE : return 16;
        case QRMode.MODE_KANJI     : return 12;
        default :
          throw 'mode:' + mode;
        }

      } else {
        throw 'type:' + type;
      }
    };

    _this.getLostPoint = function(qrcode) {

      var moduleCount = qrcode.getModuleCount();

      var lostPoint = 0;

      // LEVEL1

      for (var row = 0; row < moduleCount; row += 1) {
        for (var col = 0; col < moduleCount; col += 1) {

          var sameCount = 0;
          var dark = qrcode.isDark(row, col);

          for (var r = -1; r <= 1; r += 1) {

            if (row + r < 0 || moduleCount <= row + r) {
              continue;
            }

            for (var c = -1; c <= 1; c += 1) {

              if (col + c < 0 || moduleCount <= col + c) {
                continue;
              }

              if (r == 0 && c == 0) {
                continue;
              }

              if (dark == qrcode.isDark(row + r, col + c) ) {
                sameCount += 1;
              }
            }
          }

          if (sameCount > 5) {
            lostPoint += (3 + sameCount - 5);
          }
        }
      };

      // LEVEL2

      for (var row = 0; row < moduleCount - 1; row += 1) {
        for (var col = 0; col < moduleCount - 1; col += 1) {
          var count = 0;
          if (qrcode.isDark(row, col) ) count += 1;
          if (qrcode.isDark(row + 1, col) ) count += 1;
          if (qrcode.isDark(row, col + 1) ) count += 1;
          if (qrcode.isDark(row + 1, col + 1) ) count += 1;
          if (count == 0 || count == 4) {
            lostPoint += 3;
          }
        }
      }

      // LEVEL3

      for (var row = 0; row < moduleCount; row += 1) {
        for (var col = 0; col < moduleCount - 6; col += 1) {
          if (qrcode.isDark(row, col)
              && !qrcode.isDark(row, col + 1)
              &&  qrcode.isDark(row, col + 2)
              &&  qrcode.isDark(row, col + 3)
              &&  qrcode.isDark(row, col + 4)
              && !qrcode.isDark(row, col + 5)
              &&  qrcode.isDark(row, col + 6) ) {
            lostPoint += 40;
          }
        }
      }

      for (var col = 0; col < moduleCount; col += 1) {
        for (var row = 0; row < moduleCount - 6; row += 1) {
          if (qrcode.isDark(row, col)
              && !qrcode.isDark(row + 1, col)
              &&  qrcode.isDark(row + 2, col)
              &&  qrcode.isDark(row + 3, col)
              &&  qrcode.isDark(row + 4, col)
              && !qrcode.isDark(row + 5, col)
              &&  qrcode.isDark(row + 6, col) ) {
            lostPoint += 40;
          }
        }
      }

      // LEVEL4

      var darkCount = 0;

      for (var col = 0; col < moduleCount; col += 1) {
        for (var row = 0; row < moduleCount; row += 1) {
          if (qrcode.isDark(row, col) ) {
            darkCount += 1;
          }
        }
      }

      var ratio = Math.abs(100 * darkCount / moduleCount / moduleCount - 50) / 5;
      lostPoint += ratio * 10;

      return lostPoint;
    };

    return _this;
  }();

  //---------------------------------------------------------------------
  // QRMath
  //---------------------------------------------------------------------

  var QRMath = function() {

    var EXP_TABLE = new Array(256);
    var LOG_TABLE = new Array(256);

    // initialize tables
    for (var i = 0; i < 8; i += 1) {
      EXP_TABLE[i] = 1 << i;
    }
    for (var i = 8; i < 256; i += 1) {
      EXP_TABLE[i] = EXP_TABLE[i - 4]
        ^ EXP_TABLE[i - 5]
        ^ EXP_TABLE[i - 6]
        ^ EXP_TABLE[i - 8];
    }
    for (var i = 0; i < 255; i += 1) {
      LOG_TABLE[EXP_TABLE[i] ] = i;
    }

    var _this = {};

    _this.glog = function(n) {

      if (n < 1) {
        throw 'glog(' + n + ')';
      }

      return LOG_TABLE[n];
    };

    _this.gexp = function(n) {

      while (n < 0) {
        n += 255;
      }

      while (n >= 256) {
        n -= 255;
      }

      return EXP_TABLE[n];
    };

    return _this;
  }();

  //---------------------------------------------------------------------
  // qrPolynomial
  //---------------------------------------------------------------------

  function qrPolynomial(num, shift) {

    if (typeof num.length == 'undefined') {
      throw num.length + '/' + shift;
    }

    var _num = function() {
      var offset = 0;
      while (offset < num.length && num[offset] == 0) {
        offset += 1;
      }
      var _num = new Array(num.length - offset + shift);
      for (var i = 0; i < num.length - offset; i += 1) {
        _num[i] = num[i + offset];
      }
      return _num;
    }();

    var _this = {};

    _this.getAt = function(index) {
      return _num[index];
    };

    _this.getLength = function() {
      return _num.length;
    };

    _this.multiply = function(e) {

      var num = new Array(_this.getLength() + e.getLength() - 1);

      for (var i = 0; i < _this.getLength(); i += 1) {
        for (var j = 0; j < e.getLength(); j += 1) {
          num[i + j] ^= QRMath.gexp(QRMath.glog(_this.getAt(i) ) + QRMath.glog(e.getAt(j) ) );
        }
      }

      return qrPolynomial(num, 0);
    };

    _this.mod = function(e) {

      if (_this.getLength() - e.getLength() < 0) {
        return _this;
      }

      var ratio = QRMath.glog(_this.getAt(0) ) - QRMath.glog(e.getAt(0) );

      var num = new Array(_this.getLength() );
      for (var i = 0; i < _this.getLength(); i += 1) {
        num[i] = _this.getAt(i);
      }

      for (var i = 0; i < e.getLength(); i += 1) {
        num[i] ^= QRMath.gexp(QRMath.glog(e.getAt(i) ) + ratio);
      }

      // recursive call
      return qrPolynomial(num, 0).mod(e);
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // QRRSBlock
  //---------------------------------------------------------------------

  var QRRSBlock = function() {

    var RS_BLOCK_TABLE = [

      // L
      // M
      // Q
      // H

      // 1
      [1, 26, 19],
      [1, 26, 16],
      [1, 26, 13],
      [1, 26, 9],

      // 2
      [1, 44, 34],
      [1, 44, 28],
      [1, 44, 22],
      [1, 44, 16],

      // 3
      [1, 70, 55],
      [1, 70, 44],
      [2, 35, 17],
      [2, 35, 13],

      // 4
      [1, 100, 80],
      [2, 50, 32],
      [2, 50, 24],
      [4, 25, 9],

      // 5
      [1, 134, 108],
      [2, 67, 43],
      [2, 33, 15, 2, 34, 16],
      [2, 33, 11, 2, 34, 12],

      // 6
      [2, 86, 68],
      [4, 43, 27],
      [4, 43, 19],
      [4, 43, 15],

      // 7
      [2, 98, 78],
      [4, 49, 31],
      [2, 32, 14, 4, 33, 15],
      [4, 39, 13, 1, 40, 14],

      // 8
      [2, 121, 97],
      [2, 60, 38, 2, 61, 39],
      [4, 40, 18, 2, 41, 19],
      [4, 40, 14, 2, 41, 15],

      // 9
      [2, 146, 116],
      [3, 58, 36, 2, 59, 37],
      [4, 36, 16, 4, 37, 17],
      [4, 36, 12, 4, 37, 13],

      // 10
      [2, 86, 68, 2, 87, 69],
      [4, 69, 43, 1, 70, 44],
      [6, 43, 19, 2, 44, 20],
      [6, 43, 15, 2, 44, 16],

      // 11
      [4, 101, 81],
      [1, 80, 50, 4, 81, 51],
      [4, 50, 22, 4, 51, 23],
      [3, 36, 12, 8, 37, 13],

      // 12
      [2, 116, 92, 2, 117, 93],
      [6, 58, 36, 2, 59, 37],
      [4, 46, 20, 6, 47, 21],
      [7, 42, 14, 4, 43, 15],

      // 13
      [4, 133, 107],
      [8, 59, 37, 1, 60, 38],
      [8, 44, 20, 4, 45, 21],
      [12, 33, 11, 4, 34, 12],

      // 14
      [3, 145, 115, 1, 146, 116],
      [4, 64, 40, 5, 65, 41],
      [11, 36, 16, 5, 37, 17],
      [11, 36, 12, 5, 37, 13],

      // 15
      [5, 109, 87, 1, 110, 88],
      [5, 65, 41, 5, 66, 42],
      [5, 54, 24, 7, 55, 25],
      [11, 36, 12, 7, 37, 13],

      // 16
      [5, 122, 98, 1, 123, 99],
      [7, 73, 45, 3, 74, 46],
      [15, 43, 19, 2, 44, 20],
      [3, 45, 15, 13, 46, 16],

      // 17
      [1, 135, 107, 5, 136, 108],
      [10, 74, 46, 1, 75, 47],
      [1, 50, 22, 15, 51, 23],
      [2, 42, 14, 17, 43, 15],

      // 18
      [5, 150, 120, 1, 151, 121],
      [9, 69, 43, 4, 70, 44],
      [17, 50, 22, 1, 51, 23],
      [2, 42, 14, 19, 43, 15],

      // 19
      [3, 141, 113, 4, 142, 114],
      [3, 70, 44, 11, 71, 45],
      [17, 47, 21, 4, 48, 22],
      [9, 39, 13, 16, 40, 14],

      // 20
      [3, 135, 107, 5, 136, 108],
      [3, 67, 41, 13, 68, 42],
      [15, 54, 24, 5, 55, 25],
      [15, 43, 15, 10, 44, 16],

      // 21
      [4, 144, 116, 4, 145, 117],
      [17, 68, 42],
      [17, 50, 22, 6, 51, 23],
      [19, 46, 16, 6, 47, 17],

      // 22
      [2, 139, 111, 7, 140, 112],
      [17, 74, 46],
      [7, 54, 24, 16, 55, 25],
      [34, 37, 13],

      // 23
      [4, 151, 121, 5, 152, 122],
      [4, 75, 47, 14, 76, 48],
      [11, 54, 24, 14, 55, 25],
      [16, 45, 15, 14, 46, 16],

      // 24
      [6, 147, 117, 4, 148, 118],
      [6, 73, 45, 14, 74, 46],
      [11, 54, 24, 16, 55, 25],
      [30, 46, 16, 2, 47, 17],

      // 25
      [8, 132, 106, 4, 133, 107],
      [8, 75, 47, 13, 76, 48],
      [7, 54, 24, 22, 55, 25],
      [22, 45, 15, 13, 46, 16],

      // 26
      [10, 142, 114, 2, 143, 115],
      [19, 74, 46, 4, 75, 47],
      [28, 50, 22, 6, 51, 23],
      [33, 46, 16, 4, 47, 17],

      // 27
      [8, 152, 122, 4, 153, 123],
      [22, 73, 45, 3, 74, 46],
      [8, 53, 23, 26, 54, 24],
      [12, 45, 15, 28, 46, 16],

      // 28
      [3, 147, 117, 10, 148, 118],
      [3, 73, 45, 23, 74, 46],
      [4, 54, 24, 31, 55, 25],
      [11, 45, 15, 31, 46, 16],

      // 29
      [7, 146, 116, 7, 147, 117],
      [21, 73, 45, 7, 74, 46],
      [1, 53, 23, 37, 54, 24],
      [19, 45, 15, 26, 46, 16],

      // 30
      [5, 145, 115, 10, 146, 116],
      [19, 75, 47, 10, 76, 48],
      [15, 54, 24, 25, 55, 25],
      [23, 45, 15, 25, 46, 16],

      // 31
      [13, 145, 115, 3, 146, 116],
      [2, 74, 46, 29, 75, 47],
      [42, 54, 24, 1, 55, 25],
      [23, 45, 15, 28, 46, 16],

      // 32
      [17, 145, 115],
      [10, 74, 46, 23, 75, 47],
      [10, 54, 24, 35, 55, 25],
      [19, 45, 15, 35, 46, 16],

      // 33
      [17, 145, 115, 1, 146, 116],
      [14, 74, 46, 21, 75, 47],
      [29, 54, 24, 19, 55, 25],
      [11, 45, 15, 46, 46, 16],

      // 34
      [13, 145, 115, 6, 146, 116],
      [14, 74, 46, 23, 75, 47],
      [44, 54, 24, 7, 55, 25],
      [59, 46, 16, 1, 47, 17],

      // 35
      [12, 151, 121, 7, 152, 122],
      [12, 75, 47, 26, 76, 48],
      [39, 54, 24, 14, 55, 25],
      [22, 45, 15, 41, 46, 16],

      // 36
      [6, 151, 121, 14, 152, 122],
      [6, 75, 47, 34, 76, 48],
      [46, 54, 24, 10, 55, 25],
      [2, 45, 15, 64, 46, 16],

      // 37
      [17, 152, 122, 4, 153, 123],
      [29, 74, 46, 14, 75, 47],
      [49, 54, 24, 10, 55, 25],
      [24, 45, 15, 46, 46, 16],

      // 38
      [4, 152, 122, 18, 153, 123],
      [13, 74, 46, 32, 75, 47],
      [48, 54, 24, 14, 55, 25],
      [42, 45, 15, 32, 46, 16],

      // 39
      [20, 147, 117, 4, 148, 118],
      [40, 75, 47, 7, 76, 48],
      [43, 54, 24, 22, 55, 25],
      [10, 45, 15, 67, 46, 16],

      // 40
      [19, 148, 118, 6, 149, 119],
      [18, 75, 47, 31, 76, 48],
      [34, 54, 24, 34, 55, 25],
      [20, 45, 15, 61, 46, 16]
    ];

    var qrRSBlock = function(totalCount, dataCount) {
      var _this = {};
      _this.totalCount = totalCount;
      _this.dataCount = dataCount;
      return _this;
    };

    var _this = {};

    var getRsBlockTable = function(typeNumber, errorCorrectionLevel) {

      switch(errorCorrectionLevel) {
      case QRErrorCorrectionLevel.L :
        return RS_BLOCK_TABLE[(typeNumber - 1) * 4 + 0];
      case QRErrorCorrectionLevel.M :
        return RS_BLOCK_TABLE[(typeNumber - 1) * 4 + 1];
      case QRErrorCorrectionLevel.Q :
        return RS_BLOCK_TABLE[(typeNumber - 1) * 4 + 2];
      case QRErrorCorrectionLevel.H :
        return RS_BLOCK_TABLE[(typeNumber - 1) * 4 + 3];
      default :
        return undefined;
      }
    };

    _this.getRSBlocks = function(typeNumber, errorCorrectionLevel) {

      var rsBlock = getRsBlockTable(typeNumber, errorCorrectionLevel);

      if (typeof rsBlock == 'undefined') {
        throw 'bad rs block @ typeNumber:' + typeNumber +
            '/errorCorrectionLevel:' + errorCorrectionLevel;
      }

      var length = rsBlock.length / 3;

      var list = [];

      for (var i = 0; i < length; i += 1) {

        var count = rsBlock[i * 3 + 0];
        var totalCount = rsBlock[i * 3 + 1];
        var dataCount = rsBlock[i * 3 + 2];

        for (var j = 0; j < count; j += 1) {
          list.push(qrRSBlock(totalCount, dataCount) );
        }
      }

      return list;
    };

    return _this;
  }();

  //---------------------------------------------------------------------
  // qrBitBuffer
  //---------------------------------------------------------------------

  var qrBitBuffer = function() {

    var _buffer = [];
    var _length = 0;

    var _this = {};

    _this.getBuffer = function() {
      return _buffer;
    };

    _this.getAt = function(index) {
      var bufIndex = Math.floor(index / 8);
      return ( (_buffer[bufIndex] >>> (7 - index % 8) ) & 1) == 1;
    };

    _this.put = function(num, length) {
      for (var i = 0; i < length; i += 1) {
        _this.putBit( ( (num >>> (length - i - 1) ) & 1) == 1);
      }
    };

    _this.getLengthInBits = function() {
      return _length;
    };

    _this.putBit = function(bit) {

      var bufIndex = Math.floor(_length / 8);
      if (_buffer.length <= bufIndex) {
        _buffer.push(0);
      }

      if (bit) {
        _buffer[bufIndex] |= (0x80 >>> (_length % 8) );
      }

      _length += 1;
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // qrNumber
  //---------------------------------------------------------------------

  var qrNumber = function(data) {

    var _mode = QRMode.MODE_NUMBER;
    var _data = data;

    var _this = {};

    _this.getMode = function() {
      return _mode;
    };

    _this.getLength = function(buffer) {
      return _data.length;
    };

    _this.write = function(buffer) {

      var data = _data;

      var i = 0;

      while (i + 2 < data.length) {
        buffer.put(strToNum(data.substring(i, i + 3) ), 10);
        i += 3;
      }

      if (i < data.length) {
        if (data.length - i == 1) {
          buffer.put(strToNum(data.substring(i, i + 1) ), 4);
        } else if (data.length - i == 2) {
          buffer.put(strToNum(data.substring(i, i + 2) ), 7);
        }
      }
    };

    var strToNum = function(s) {
      var num = 0;
      for (var i = 0; i < s.length; i += 1) {
        num = num * 10 + chatToNum(s.charAt(i) );
      }
      return num;
    };

    var chatToNum = function(c) {
      if ('0' <= c && c <= '9') {
        return c.charCodeAt(0) - '0'.charCodeAt(0);
      }
      throw 'illegal char :' + c;
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // qrAlphaNum
  //---------------------------------------------------------------------

  var qrAlphaNum = function(data) {

    var _mode = QRMode.MODE_ALPHA_NUM;
    var _data = data;

    var _this = {};

    _this.getMode = function() {
      return _mode;
    };

    _this.getLength = function(buffer) {
      return _data.length;
    };

    _this.write = function(buffer) {

      var s = _data;

      var i = 0;

      while (i + 1 < s.length) {
        buffer.put(
          getCode(s.charAt(i) ) * 45 +
          getCode(s.charAt(i + 1) ), 11);
        i += 2;
      }

      if (i < s.length) {
        buffer.put(getCode(s.charAt(i) ), 6);
      }
    };

    var getCode = function(c) {

      if ('0' <= c && c <= '9') {
        return c.charCodeAt(0) - '0'.charCodeAt(0);
      } else if ('A' <= c && c <= 'Z') {
        return c.charCodeAt(0) - 'A'.charCodeAt(0) + 10;
      } else {
        switch (c) {
        case ' ' : return 36;
        case '$' : return 37;
        case '%' : return 38;
        case '*' : return 39;
        case '+' : return 40;
        case '-' : return 41;
        case '.' : return 42;
        case '/' : return 43;
        case ':' : return 44;
        default :
          throw 'illegal char :' + c;
        }
      }
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // qr8BitByte
  //---------------------------------------------------------------------

  var qr8BitByte = function(data) {

    var _mode = QRMode.MODE_8BIT_BYTE;
    var _data = data;
    var _bytes = qrcode.stringToBytes(data);

    var _this = {};

    _this.getMode = function() {
      return _mode;
    };

    _this.getLength = function(buffer) {
      return _bytes.length;
    };

    _this.write = function(buffer) {
      for (var i = 0; i < _bytes.length; i += 1) {
        buffer.put(_bytes[i], 8);
      }
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // qrKanji
  //---------------------------------------------------------------------

  var qrKanji = function(data) {

    var _mode = QRMode.MODE_KANJI;
    var _data = data;

    var stringToBytes = qrcode.stringToBytesFuncs['SJIS'];
    if (!stringToBytes) {
      throw 'sjis not supported.';
    }
    !function(c, code) {
      // self test for sjis support.
      var test = stringToBytes(c);
      if (test.length != 2 || ( (test[0] << 8) | test[1]) != code) {
        throw 'sjis not supported.';
      }
    }('\u53cb', 0x9746);

    var _bytes = stringToBytes(data);

    var _this = {};

    _this.getMode = function() {
      return _mode;
    };

    _this.getLength = function(buffer) {
      return ~~(_bytes.length / 2);
    };

    _this.write = function(buffer) {

      var data = _bytes;

      var i = 0;

      while (i + 1 < data.length) {

        var c = ( (0xff & data[i]) << 8) | (0xff & data[i + 1]);

        if (0x8140 <= c && c <= 0x9FFC) {
          c -= 0x8140;
        } else if (0xE040 <= c && c <= 0xEBBF) {
          c -= 0xC140;
        } else {
          throw 'illegal char at ' + (i + 1) + '/' + c;
        }

        c = ( (c >>> 8) & 0xff) * 0xC0 + (c & 0xff);

        buffer.put(c, 13);

        i += 2;
      }

      if (i < data.length) {
        throw 'illegal char at ' + (i + 1);
      }
    };

    return _this;
  };

  //=====================================================================
  // GIF Support etc.
  //

  //---------------------------------------------------------------------
  // byteArrayOutputStream
  //---------------------------------------------------------------------

  var byteArrayOutputStream = function() {

    var _bytes = [];

    var _this = {};

    _this.writeByte = function(b) {
      _bytes.push(b & 0xff);
    };

    _this.writeShort = function(i) {
      _this.writeByte(i);
      _this.writeByte(i >>> 8);
    };

    _this.writeBytes = function(b, off, len) {
      off = off || 0;
      len = len || b.length;
      for (var i = 0; i < len; i += 1) {
        _this.writeByte(b[i + off]);
      }
    };

    _this.writeString = function(s) {
      for (var i = 0; i < s.length; i += 1) {
        _this.writeByte(s.charCodeAt(i) );
      }
    };

    _this.toByteArray = function() {
      return _bytes;
    };

    _this.toString = function() {
      var s = '';
      s += '[';
      for (var i = 0; i < _bytes.length; i += 1) {
        if (i > 0) {
          s += ',';
        }
        s += _bytes[i];
      }
      s += ']';
      return s;
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // base64EncodeOutputStream
  //---------------------------------------------------------------------

  var base64EncodeOutputStream = function() {

    var _buffer = 0;
    var _buflen = 0;
    var _length = 0;
    var _base64 = '';

    var _this = {};

    var writeEncoded = function(b) {
      _base64 += String.fromCharCode(encode(b & 0x3f) );
    };

    var encode = function(n) {
      if (n < 0) {
        // error.
      } else if (n < 26) {
        return 0x41 + n;
      } else if (n < 52) {
        return 0x61 + (n - 26);
      } else if (n < 62) {
        return 0x30 + (n - 52);
      } else if (n == 62) {
        return 0x2b;
      } else if (n == 63) {
        return 0x2f;
      }
      throw 'n:' + n;
    };

    _this.writeByte = function(n) {

      _buffer = (_buffer << 8) | (n & 0xff);
      _buflen += 8;
      _length += 1;

      while (_buflen >= 6) {
        writeEncoded(_buffer >>> (_buflen - 6) );
        _buflen -= 6;
      }
    };

    _this.flush = function() {

      if (_buflen > 0) {
        writeEncoded(_buffer << (6 - _buflen) );
        _buffer = 0;
        _buflen = 0;
      }

      if (_length % 3 != 0) {
        // padding
        var padlen = 3 - _length % 3;
        for (var i = 0; i < padlen; i += 1) {
          _base64 += '=';
        }
      }
    };

    _this.toString = function() {
      return _base64;
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // base64DecodeInputStream
  //---------------------------------------------------------------------

  var base64DecodeInputStream = function(str) {

    var _str = str;
    var _pos = 0;
    var _buffer = 0;
    var _buflen = 0;

    var _this = {};

    _this.read = function() {

      while (_buflen < 8) {

        if (_pos >= _str.length) {
          if (_buflen == 0) {
            return -1;
          }
          throw 'unexpected end of file./' + _buflen;
        }

        var c = _str.charAt(_pos);
        _pos += 1;

        if (c == '=') {
          _buflen = 0;
          return -1;
        } else if (c.match(/^\s$/) ) {
          // ignore if whitespace.
          continue;
        }

        _buffer = (_buffer << 6) | decode(c.charCodeAt(0) );
        _buflen += 6;
      }

      var n = (_buffer >>> (_buflen - 8) ) & 0xff;
      _buflen -= 8;
      return n;
    };

    var decode = function(c) {
      if (0x41 <= c && c <= 0x5a) {
        return c - 0x41;
      } else if (0x61 <= c && c <= 0x7a) {
        return c - 0x61 + 26;
      } else if (0x30 <= c && c <= 0x39) {
        return c - 0x30 + 52;
      } else if (c == 0x2b) {
        return 62;
      } else if (c == 0x2f) {
        return 63;
      } else {
        throw 'c:' + c;
      }
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // gifImage (B/W)
  //---------------------------------------------------------------------

  var gifImage = function(width, height) {

    var _width = width;
    var _height = height;
    var _data = new Array(width * height);

    var _this = {};

    _this.setPixel = function(x, y, pixel) {
      _data[y * _width + x] = pixel;
    };

    _this.write = function(out) {

      //---------------------------------
      // GIF Signature

      out.writeString('GIF87a');

      //---------------------------------
      // Screen Descriptor

      out.writeShort(_width);
      out.writeShort(_height);

      out.writeByte(0x80); // 2bit
      out.writeByte(0);
      out.writeByte(0);

      //---------------------------------
      // Global Color Map

      // black
      out.writeByte(0x00);
      out.writeByte(0x00);
      out.writeByte(0x00);

      // white
      out.writeByte(0xff);
      out.writeByte(0xff);
      out.writeByte(0xff);

      //---------------------------------
      // Image Descriptor

      out.writeString(',');
      out.writeShort(0);
      out.writeShort(0);
      out.writeShort(_width);
      out.writeShort(_height);
      out.writeByte(0);

      //---------------------------------
      // Local Color Map

      //---------------------------------
      // Raster Data

      var lzwMinCodeSize = 2;
      var raster = getLZWRaster(lzwMinCodeSize);

      out.writeByte(lzwMinCodeSize);

      var offset = 0;

      while (raster.length - offset > 255) {
        out.writeByte(255);
        out.writeBytes(raster, offset, 255);
        offset += 255;
      }

      out.writeByte(raster.length - offset);
      out.writeBytes(raster, offset, raster.length - offset);
      out.writeByte(0x00);

      //---------------------------------
      // GIF Terminator
      out.writeString(';');
    };

    var bitOutputStream = function(out) {

      var _out = out;
      var _bitLength = 0;
      var _bitBuffer = 0;

      var _this = {};

      _this.write = function(data, length) {

        if ( (data >>> length) != 0) {
          throw 'length over';
        }

        while (_bitLength + length >= 8) {
          _out.writeByte(0xff & ( (data << _bitLength) | _bitBuffer) );
          length -= (8 - _bitLength);
          data >>>= (8 - _bitLength);
          _bitBuffer = 0;
          _bitLength = 0;
        }

        _bitBuffer = (data << _bitLength) | _bitBuffer;
        _bitLength = _bitLength + length;
      };

      _this.flush = function() {
        if (_bitLength > 0) {
          _out.writeByte(_bitBuffer);
        }
      };

      return _this;
    };

    var getLZWRaster = function(lzwMinCodeSize) {

      var clearCode = 1 << lzwMinCodeSize;
      var endCode = (1 << lzwMinCodeSize) + 1;
      var bitLength = lzwMinCodeSize + 1;

      // Setup LZWTable
      var table = lzwTable();

      for (var i = 0; i < clearCode; i += 1) {
        table.add(String.fromCharCode(i) );
      }
      table.add(String.fromCharCode(clearCode) );
      table.add(String.fromCharCode(endCode) );

      var byteOut = byteArrayOutputStream();
      var bitOut = bitOutputStream(byteOut);

      // clear code
      bitOut.write(clearCode, bitLength);

      var dataIndex = 0;

      var s = String.fromCharCode(_data[dataIndex]);
      dataIndex += 1;

      while (dataIndex < _data.length) {

        var c = String.fromCharCode(_data[dataIndex]);
        dataIndex += 1;

        if (table.contains(s + c) ) {

          s = s + c;

        } else {

          bitOut.write(table.indexOf(s), bitLength);

          if (table.size() < 0xfff) {

            if (table.size() == (1 << bitLength) ) {
              bitLength += 1;
            }

            table.add(s + c);
          }

          s = c;
        }
      }

      bitOut.write(table.indexOf(s), bitLength);

      // end code
      bitOut.write(endCode, bitLength);

      bitOut.flush();

      return byteOut.toByteArray();
    };

    var lzwTable = function() {

      var _map = {};
      var _size = 0;

      var _this = {};

      _this.add = function(key) {
        if (_this.contains(key) ) {
          throw 'dup key:' + key;
        }
        _map[key] = _size;
        _size += 1;
      };

      _this.size = function() {
        return _size;
      };

      _this.indexOf = function(key) {
        return _map[key];
      };

      _this.contains = function(key) {
        return typeof _map[key] != 'undefined';
      };

      return _this;
    };

    return _this;
  };

  var createDataURL = function(width, height, getPixel) {
    var gif = gifImage(width, height);
    for (var y = 0; y < height; y += 1) {
      for (var x = 0; x < width; x += 1) {
        gif.setPixel(x, y, getPixel(x, y) );
      }
    }

    var b = byteArrayOutputStream();
    gif.write(b);

    var base64 = base64EncodeOutputStream();
    var bytes = b.toByteArray();
    for (var i = 0; i < bytes.length; i += 1) {
      base64.writeByte(bytes[i]);
    }
    base64.flush();

    return 'data:image/gif;base64,' + base64;
  };

  //---------------------------------------------------------------------
  // returns qrcode function.

  return qrcode;
}();

// multibyte support
!function() {

  qrcode.stringToBytesFuncs['UTF-8'] = function(s) {
    // http://stackoverflow.com/questions/18729405/how-to-convert-utf8-string-to-byte-array
    function toUTF8Array(str) {
      var utf8 = [];
      for (var i=0; i < str.length; i++) {
        var charcode = str.charCodeAt(i);
        if (charcode < 0x80) utf8.push(charcode);
        else if (charcode < 0x800) {
          utf8.push(0xc0 | (charcode >> 6),
              0x80 | (charcode & 0x3f));
        }
        else if (charcode < 0xd800 || charcode >= 0xe000) {
          utf8.push(0xe0 | (charcode >> 12),
              0x80 | ((charcode>>6) & 0x3f),
              0x80 | (charcode & 0x3f));
        }
        // surrogate pair
        else {
          i++;
          // UTF-16 encodes 0x10000-0x10FFFF by
          // subtracting 0x10000 and splitting the
          // 20 bits of 0x0-0xFFFFF into two halves
          charcode = 0x10000 + (((charcode & 0x3ff)<<10)
            | (str.charCodeAt(i) & 0x3ff));
          utf8.push(0xf0 | (charcode >>18),
              0x80 | ((charcode>>12) & 0x3f),
              0x80 | ((charcode>>6) & 0x3f),
              0x80 | (charcode & 0x3f));
        }
      }
      return utf8;
    }
    return toUTF8Array(s);
  };

}();

(function (factory) {
  if (typeof define === 'function' && define.amd) {
      define([], factory);
  } else if (typeof exports === 'object') {
      module.exports = factory();
  }
}(function () {
    return qrcode;
}));

"""


def _err(r, d="", **e):
    return {"ok": False, "error": r, "diagnosable": d, **e}


def _rt() -> SimplexRuntime:
    return SimplexRuntime.instance()


# ────────────────────────────────────────────────────────────────────── #
# 后端 API 实现
# ────────────────────────────────────────────────────────────────────── #

def api_status() -> dict[str, Any]:
    rt = _rt()
    running = rt._thread is not None and rt._thread.is_alive()
    if not running:
        return _ok({"running": False})
    st_ = rt.status()
    contacts = rt.list_contacts()
    return _ok({
        "running": True,
        "active_user": st_.get("active_user"),
        "server": st_.get("smp_server"),
        "contacts": contacts,
    })


def api_setup(display_name: str = "") -> dict[str, Any]:
    rt = _rt()
    if rt._thread and rt._thread.is_alive():
        return _ok({"running": True, "note": "已在运行"})
    name = display_name or DM_IDENTITY
    args: dict[str, Any] = {"display_name": name}
    if DM_DB_PREFIX:
        args["db_prefix"] = DM_DB_PREFIX
    r = st.call_tool("simplex_setup", args)
    return r


def api_create_invite() -> dict[str, Any]:
    return st.call_tool("simplex_create_invitation", {})


def api_get_identity() -> dict[str, Any]:
    """返回当前身份显示名(自定义 ID)。"""
    rt = _rt()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("runtime 未启动", "先 setup")
    try:
        stt = rt.status()
        name = stt.get("active_user") or rt._display_name
    except Exception:  # noqa: BLE001
        name = rt._display_name
    return _ok({"display_name": name}, display_name=name)


def api_set_identity(new_name: str) -> dict[str, Any]:
    """改自己的显示名(自定义 ID)。对方端需要刷新/重连后看到新名。"""
    new_name = (new_name or "").strip()
    if not new_name:
        return _err("名为空", "填一个非空的显示名。")
    if len(new_name) > 48:
        return _err("名太长", "显示名建议 ≤48 字符。")
    rt = _rt()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("runtime 未启动", "先 setup")
    try:
        rt.update_display_name(new_name)
        return _ok({"display_name": new_name}, display_name=new_name, diagnosable="已改名;对方端刷新后显示新名。")
    except Exception as e:  # noqa: BLE001
        return _err("改名失败", f"{e!r}")


def _db_key_path() -> Path:
    prefix = DM_DB_PREFIX or str(Path.home() / ".local" / "share" / "aureon" / "simplex" / f"{DM_IDENTITY}_simplex")
    return Path(prefix).parent / (Path(prefix).name + ".key")


def api_db_password_status() -> dict[str, Any]:
    """是否已设聊天记录口令(密钥文件是否已生成)。"""
    return _ok({"encrypted": _db_key_path().exists()}, encrypted=_db_key_path().exists())


def api_db_set_password(password: str) -> dict[str, Any]:
    """设置聊天记录口令:生成 DB 加密密钥(口令派生),下次启动需输入口令解锁。
    注意:对【已存在的明文库】不自动迁移——官方做法也是新建加密库。此处落地
    密钥派生+口令门;明文→加密的库迁移(sqlcipher_export)留作后续,先对新库生效。"""
    import hashlib
    import secrets
    password = password or ""
    if len(password) < 4:
        return _err("口令太短", "口令至少 4 位。")
    kp = _db_key_path()
    if kp.exists():
        return _err("已设过口令", "该身份已设口令。要改口令请先验证旧口令(改密功能后续)。")
    # 口令派生 DB 密钥:scrypt(口令, 随机 salt) → 32B hex;文件 0600 存 salt+hash
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    kp.parent.mkdir(parents=True, exist_ok=True)
    kp.write_text(salt.hex() + ":" + key.hex(), encoding="utf-8")
    try:
        os.chmod(kp, 0o600)
    except Exception:  # noqa: BLE001
        pass
    return _ok({"encrypted": True}, diagnosable="口令已设置。重启后会要求输入口令解锁聊天记录;请务必记牢,丢失无法恢复。")


def api_db_unlock(password: str) -> dict[str, Any]:
    """用口令解锁:校验口令是否匹配已存密钥。匹配则把密钥注入进程环境并允许启动。"""
    import hashlib
    kp = _db_key_path()
    if not kp.exists():
        return _err("未设口令", "该身份未设口令,无需解锁。")
    salt_hex, key_hex = kp.read_text(encoding="utf-8").strip().split(":")
    key = hashlib.scrypt((password or "").encode("utf-8"), salt=bytes.fromhex(salt_hex), n=16384, r=8, p=1, dklen=32)
    if key.hex() != key_hex:
        return _err("口令错误", "口令不对,无法解锁聊天记录。")
    os.environ["DM_DB_KEY"] = key_hex  # 供 runtime start 注入 SqliteDb.encryption_key
    return _ok({"unlocked": True}, diagnosable="已解锁,正在打开聊天记录…")


def api_db_change_password(old_password: str, new_password: str) -> dict[str, Any]:
    """变更口令:先验证旧口令,再写入新口令派生的密钥。

    说明:这是「派生密钥」层面的变更 —— 与首次设口令同一条 scrypt 派生路径,
    新密钥写回密钥文件并注入环境。真正的 SQLCipher 库 rekey(sqlcipher_export
    整库迁移)留作后续;当前对「下次启动用新口令派生的密钥」生效。
    """
    import hashlib
    import secrets
    kp = _db_key_path()
    if not kp.exists():
        return _err("未设口令", "该身份未设口令,请直接「设置口令」。")
    new_password = new_password or ""
    if len(new_password) < 4:
        return _err("新口令太短", "新口令至少 4 位。")
    # 验证旧口令
    salt_hex, key_hex = kp.read_text(encoding="utf-8").strip().split(":")
    old_key = hashlib.scrypt((old_password or "").encode("utf-8"), salt=bytes.fromhex(salt_hex), n=16384, r=8, p=1, dklen=32)
    if old_key.hex() != key_hex:
        return _err("旧口令错误", "旧口令不对,无法变更。")
    # 写入新口令派生的密钥
    new_salt = secrets.token_bytes(16)
    new_key = hashlib.scrypt(new_password.encode("utf-8"), salt=new_salt, n=16384, r=8, p=1, dklen=32)
    kp.write_text(new_salt.hex() + ":" + new_key.hex(), encoding="utf-8")
    try:
        os.chmod(kp, 0o600)
    except Exception:  # noqa: BLE001
        pass
    os.environ["DM_DB_KEY"] = new_key.hex()
    return _ok({"changed": True}, diagnosable="口令已变更。下次启动用新口令解锁;请务必记牢新口令。")


def api_new_user_id() -> dict[str, Any]:
    """忘记口令 → 新建用户ID:把打不开的加密库归档到一边,以全新空库重启。

    旧库不删除,改名加 .locked-<时间戳> 后缀保留(万一以后想起口令可手动恢复)。
    新库不带口令(除非用户再设)。旧聊天记录在新库里不可见 —— 这是用户已接受的取舍。
    """
    import shutil
    kp = _db_key_path()
    prefix = DM_DB_PREFIX or str(Path.home() / ".local" / "share" / "aureon" / "simplex" / f"{DM_IDENTITY}_simplex")
    rt = _rt()
    ts = time.strftime("%Y%m%d-%H%M%S")
    try:
        # 停掉当前 runtime(若活着),释放 DB 文件句柄
        try:
            if rt._thread and rt._thread.is_alive():
                rt.shutdown()
        except Exception:  # noqa: BLE001
            pass
        os.environ.pop("DM_DB_KEY", None)  # 清掉旧密钥,新库不带口令
        # 归档旧库文件(同目录,prefix 开头的那几个 SQLite 文件)
        parent = Path(prefix).parent
        base = Path(prefix).name
        moved = []
        for f in parent.glob(base + "*"):
            if f.name == base + ".key":
                continue  # key 文件单独处理
            dest = f.with_name(f.name + f".locked-{ts}")
            try:
                shutil.move(str(f), str(dest))
                moved.append(dest.name)
            except Exception:  # noqa: BLE001
                pass
        # 归档 key 文件(否则新库仍被当"已设口令",且旧 key 对新库无意义)
        if kp.exists():
            try:
                shutil.move(str(kp), str(kp.with_name(kp.name + f".locked-{ts}")))
                moved.append(kp.name + f".locked-{ts}")
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        return _err("归档旧库失败", f"{e!r}。可手动把 {prefix}* 改名后重启。")
    # 以同一身份名重开一个全新空库
    name = DM_IDENTITY
    args: dict[str, Any] = {"display_name": name}
    if DM_DB_PREFIX:
        args["db_prefix"] = DM_DB_PREFIX
    r = st.call_tool("simplex_setup", args)
    if not r.get("ok"):
        return _err("新建用户ID失败(旧库已归档)", r.get("diagnosable", r.get("error", "")))
    return _ok(
        {"restarted": True, "archived": moved},
        diagnosable=f"已新建用户ID(旧库已归档为 .locked-{ts},不会删除)。原聊天记录在新库不可见。",
    )


def api_create_invite_incognito() -> dict[str, Any]:
    """一次性邀请 + incognito(匿名随机档案,不暴露你的显示名)。"""
    rt = _rt()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("runtime 未启动", "先 setup")
    try:
        link = rt.create_invitation(incognito=True)
        return _ok({"link": link}, link=link, diagnosable="匿名一次性邀请:对方看到的是随机生成的临时档案,不是你的显示名。")
    except Exception as e:  # noqa: BLE001
        return _err("生成匿名邀请失败", f"{e!r}")


def api_create_address() -> dict[str, Any]:
    """长期有效的用户地址(公共二维码,可多人反复扫码)。"""
    rt = _rt()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("runtime 未启动", "先 setup")
    try:
        link = rt.create_user_address()
        return _ok({"link": link}, link=link, diagnosable="长期有效地址,可无限次扫码添加;泄露可在官方客户端删除地址。")
    except Exception as e:  # noqa: BLE001
        return _err("生成长期地址失败", f"{e!r}")


def api_contacts() -> dict[str, Any]:
    rt = _rt()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("runtime 未启动", "先 setup")
    contacts = rt.list_contacts()
    # 每个联系人附带未读/最近一条预览
    out = []
    for c in contacts:
        cid = c["contact_id"]
        try:
            texts = rt.chat_texts(cid, limit=1)
            preview = texts[-1][:60] if texts else ""
        except Exception:  # noqa: BLE001
            preview = ""
        out.append({**c, "preview": preview})
    return _ok(out)


def api_delete_contact(contact: str) -> dict[str, Any]:
    return st.call_tool("simplex_delete_contact", {"contact": contact})


def api_history(contact: str, limit: int = 60) -> dict[str, Any]:
    rt = _rt()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("runtime 未启动", "先 setup")
    resolved = rt.resolve_contact(contact)
    if resolved is None:
        return _err(f"没有联系人 {contact}", "先接受邀请")
    cid = resolved["contact_id"]
    # 用 chat_items(带方向 me/them + 完整历史),不是 chat_texts(无方向,易只显示一条)
    items = rt.chat_items(cid, limit=limit)
    msgs = [{"id": it["id"], "dir": it["dir"], "kind": it["kind"], "text": it["text"], "ts": it["ts"]} for it in items]
    # 待下载文件邀请
    files = rt.list_inbox_files(cid)
    return _ok({"contact": resolved, "messages": msgs, "incoming_files": files})


def api_send(contact: str, text: str, ttl: int = 0) -> dict[str, Any]:
    """发消息;ttl>0 走阅后即焚(协议级自毁,秒),否则普通消息。"""
    if ttl and int(ttl) > 0:
        return st.call_tool("simplex_send_message_ttl", {"contact": contact, "text": text, "ttl": int(ttl)})
    return st.call_tool("simplex_send_message", {"contact": contact, "text": text})


# ── 2 人 E2E 通话(轻量信令经 E2E 加密通道,媒体 P2P WebRTC)───────────────
# 信令 = JSON 消息,前缀 [DMCALL];经 simplex_send_message(已 E2E 加密)传输。
# 浏览器侧原生 RTCPeerConnection 收发媒体;后端只搬信令,不碰媒体。
_CALL_PREFIX = "[DMCALL]"


def api_call_signal(contact: str, signal: dict) -> dict[str, Any]:
    """把一条通话信令(offer/answer/ice/end)作为 JSON 消息发给联系人。"""
    import json as _json
    payload = _CALL_PREFIX + _json.dumps(signal, ensure_ascii=False)
    return st.call_tool("simplex_send_message", {"contact": contact, "text": payload})


def api_call_poll(contact: str, since_id: int = 0) -> dict[str, Any]:
    """拉取该联系人最近的通话信令([DMCALL] 前缀的消息)。

    返回 [{id, dir, signal, ts}],id = chat item 的稳定 itemId。
    **增量按 since_id(消息 id),不是时间戳** —— 时间戳跨时钟比较不可靠
    (服务器 UTC vs 浏览器本地钟,毫秒/格式不一),曾导致"等待接听无反应"。
    since_id:只返回 itemId 大于它的信令。"""
    rt = _rt()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("runtime 未启动", "先 setup")
    resolved = rt.resolve_contact(contact)
    if resolved is None:
        return _err(f"没有联系人 {contact}", "先接受邀请")
    cid = resolved["contact_id"]
    items = rt.chat_items(cid, limit=60)
    import json as _json
    out = []
    for it in items:
        t = it.get("text", "")
        if not t.startswith(_CALL_PREFIX):
            continue
        iid = it.get("id") or 0
        if since_id and iid <= since_id:
            continue
        try:
            sig = _json.loads(t[len(_CALL_PREFIX):])
        except Exception:  # noqa: BLE001
            continue
        out.append({"id": iid, "dir": it["dir"], "signal": sig, "ts": it.get("ts", "")})
    return _ok(out)


def api_receive_file(file_id: int) -> dict[str, Any]:
    r = sf.call_tool("simplex_receive_file", {"file_id": file_id, "timeout": 60})
    # 下载成功后:若用户设过自定义保存目录,复制一份过去并在结果里带上可见路径
    if r.get("ok"):
        saved = (r.get("output") or {}).get("saved_path") or ""
        custom = _download_dir_path().read_text(encoding="utf-8").strip() if _download_dir_path().exists() else ""
        shown = saved
        if custom and saved and Path(saved).is_file():
            try:
                import shutil
                Path(custom).mkdir(parents=True, exist_ok=True)
                dest = Path(custom) / Path(saved).name
                shutil.copy2(saved, dest)
                shown = str(dest)
                r.setdefault("output", {})["copied_to"] = str(dest)
            except Exception:  # noqa: BLE001
                pass
        r.setdefault("output", {})["display_path"] = shown
    return r


def _download_dir_path() -> Path:
    prefix = DM_DB_PREFIX or str(Path.home() / ".local" / "share" / "aureon" / "simplex" / f"{DM_IDENTITY}_simplex")
    return Path(prefix).parent / "download_dir.txt"


def api_get_download_dir() -> dict[str, Any]:
    """当前生效的下载保存目录:自定义(若设过)否则默认 simplex 下载目录。"""
    rt = _rt()
    default = getattr(rt, "_file_download_dir", "") or str(
        (Path(DM_DB_PREFIX) if DM_DB_PREFIX else Path.home() / ".local" / "share" / "aureon" / "simplex" / f"{DM_IDENTITY}_simplex").parent
        / "simplex_files_root" / "downloads"
    )
    custom = _download_dir_path().read_text(encoding="utf-8").strip() if _download_dir_path().exists() else ""
    return _ok({"default": default, "custom": custom, "effective": custom or default})


def api_set_download_dir(path: str) -> dict[str, Any]:
    """设置自定义下载保存目录(壳选文件夹后回填)。空串 = 恢复默认。"""
    path = (path or "").strip()
    p = _download_dir_path()
    if not path:
        if p.exists():
            p.unlink()
        return _ok({"effective": None}, diagnosable="已恢复默认下载目录。")
    tgt = Path(path).expanduser()
    try:
        tgt.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return _err("目录不可用", f"{e!r}")
    if not tgt.is_dir():
        return _err("不是目录", f"{tgt} 不是有效目录。")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(tgt), encoding="utf-8")
    return _ok({"effective": str(tgt)}, diagnosable=f"下载将保存到 {tgt}(同时在 simplex 目录留底)。")


def api_verify_file(contact: str, file_name: str) -> dict[str, Any]:
    return si.call_tool("simplex_verify_file_by_manifest", {"contact": contact, "file_name": file_name})


def api_send_file_signed(contact: str, path: str) -> dict[str, Any]:
    return si.call_tool("simplex_send_file_signed", {"contact": contact, "path": path})


def api_trust_establish(contact: str) -> dict[str, Any]:
    return si.call_tool("simplex_trust_establish", {"contact": contact})


def api_accept_invite(link: str) -> dict[str, Any]:
    return st.call_tool("simplex_accept_invitation", {"link": link, "timeout": 100})


def api_trust_import(contact: str, key: str) -> dict[str, Any]:
    return si.call_tool("simplex_trust_import", {"contact": contact, "key": key})


def api_send_file(contact: str, path: str, signed: bool) -> dict[str, Any]:
    """发送文件(可选带签名清单)。signed=true 走 simplex_send_file_signed。
    路径来自壳原生选文件对话框(用户亲手选)→ 先把其目录并入本进程允许范围,
    否则手填白名单外路径会被 _resolve_sendable 拒。
    统一签名 UX:signed 且无信任根时自动先 trust_establish(经 E2E 通道交换密钥),
    不再要求用户去联系人区点隐藏的 🔑 —— 发送即建立信任,一步到位。"""
    if path:
        try:
            sf.register_send_root(path)
        except Exception:  # noqa: BLE001
            pass
    if signed:
        # 自动建立信任(幂等:已建立则内部快速返回/复用,不会重复打扰对方)
        tr = si.call_tool("simplex_trust_establish", {"contact": contact})
        r = si.call_tool("simplex_send_file_signed", {"contact": contact, "path": path})
        if tr.get("ok") and isinstance(r.get("output"), dict):
            r["output"]["auto_trust"] = True
        return r
    return sf.call_tool("simplex_send_file", {"contact": contact, "path": path})


def api_file_info(path: str) -> dict[str, Any]:
    return si.call_tool("simplex_file_info", {"path": path})


def api_a2h_status() -> dict[str, Any]:
    """A2H 审批状态:approver + 待裁决列表(供 UI 显示审批卡)。"""
    rt = _rt()
    pend = sa.call_tool("simplex_a2h_pending", {})
    return _ok({
        "approver": rt._a2h_approver_name,
        "approver_cid": rt._a2h_approver_cid,
        "pending": pend.get("output", []) if pend.get("ok") else [],
    })


def api_a2h_set_approver(contact: str) -> dict[str, Any]:
    return sa.call_tool("simplex_a2h_set_approver", {"contact": contact})


# ────────────────────────────────────────────────────────────────────── #
# 嵌入式 IM 界面
# ────────────────────────────────────────────────────────────────────── #

_PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SecureDM · 加密私信</title>
<style>
  :root{--bg:#0f1115;--panel:#171a21;--line:#23262f;--txt:#e6e8ec;--dim:#9aa3b2;--acc:#4c8dff;--ok:#3fb27f;--warn:#e0a34a;--bad:#e06a6a;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 -apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif;height:100vh;display:flex;flex-direction:column}
  header{padding:11px 16px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}
  header .dot{width:9px;height:9px;border-radius:50%;background:var(--ok)}
  header h1{font-size:16px;margin:0;font-weight:600}
  header .st{margin-left:auto;font-size:12px;color:var(--dim)}
  #main{flex:1;display:flex;min-height:0}
  #contacts{width:250px;border-right:1px solid var(--line);overflow-y:auto;display:flex;flex-direction:column}
  #contacts .hd{padding:12px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}
  #contacts .hd b{font-size:14px}
  #contacts .hd button{background:var(--acc);border:none;color:#fff;padding:5px 10px;border-radius:7px;cursor:pointer;font-size:12px}
  #contacts .hd2{padding:8px 10px;border-bottom:1px solid var(--line);display:flex;gap:6px;flex-wrap:wrap}
  #contacts .hd2 button{background:var(--panel);border:1px solid #2a2e38;color:var(--txt);padding:6px 9px;border-radius:7px;cursor:pointer;font-size:12px;flex:1;min-width:64px;white-space:nowrap}
  #contacts .hd2 button:hover{border-color:var(--acc);color:var(--acc)}
  .citem{padding:11px 12px;border-bottom:1px solid #1a1d24;cursor:pointer}
  .citem:hover{background:#1a1e26}
  .citem.active{background:#1d2330}
  .citem .nm{font-weight:600;font-size:14px}
  .citem .pv{font-size:12px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #chat{flex:1;display:flex;flex-direction:column;min-width:0}
  #msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:6px}
  /* IM 布局惯例(参照 WeChat/iMessage/WhatsApp 调研):
     我=右侧+品牌色,对方=左侧+中性色;气泡角在发送方一侧收平;系统消息居中灰色小字。
     位置+颜色+形状三重区分,不单靠颜色(色盲/暗色模式友好)。 */
  .m{max-width:78%;padding:9px 13px;font-size:15px;line-height:1.4;white-space:pre-wrap;word-break:break-word;box-shadow:0 1px 2px rgba(0,0,0,.28)}
  /* 我:右侧,品牌蓝,右下角收平(LTR 发送方侧) */
  .m.me{align-self:flex-end;background:linear-gradient(135deg,#3b82d6,#2b6cb0);color:#fff;border-radius:18px 18px 4px 18px}
  /* 对方:左侧,中性深灰 + 左边框,左下角收平 */
  .m.them{align-self:flex-start;background:#232833;color:#e6e8ec;border-left:3px solid #4a5568;border-radius:18px 18px 18px 4px}
  /* 系统/签名清单:居中、灰小字、无气泡感 */
  .m.manifest{align-self:center;background:rgba(255,255,255,.05);border:1px dashed #3a4150;font-size:12px;color:var(--dim);border-radius:8px;max-width:88%}
  .vbadge{display:inline-flex;align-items:center;gap:5px;font-size:12px;padding:3px 9px;border-radius:11px;margin-top:5px}
  .vbadge.ok{background:#153326;color:var(--ok);border:1px solid var(--ok)}
  .vbadge.bad{background:#3a1d1d;color:var(--bad);border:1px solid var(--bad)}
  .vbadge.pending{background:#2a2e38;color:var(--dim)}
  .fmsg{align-self:flex-start;background:#1e222b;padding:10px 12px;border-radius:11px;border:1px solid #2a2e38}
  .fmsg .fname{font-weight:600}
  .fmsg button{background:var(--acc);border:none;color:#fff;padding:5px 12px;border-radius:7px;cursor:pointer;font-size:12px;margin-top:6px}
  .sys{align-self:center;font-size:12px;color:var(--dim)}
  #composer{display:flex;gap:9px;padding:13px 16px;border-top:1px solid var(--line)}
  #in{flex:1;background:var(--panel);border:1px solid #2a2e38;color:var(--txt);border-radius:9px;padding:10px 12px;outline:none}
  #in:focus{border-color:var(--acc)}
  #composer button{background:var(--acc);border:none;color:#fff;padding:0 17px;border-radius:9px;cursor:pointer;font-weight:600}
  #empty{flex:1;display:flex;align-items:center;justify-content:center;color:var(--dim);flex-direction:column;gap:10px}
  #inviteModal,#acceptModal,#settingsModal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center}
  #inviteModal .box,#acceptModal .box,#settingsModal .box{background:var(--panel);padding:20px;border-radius:12px;max-width:560px;width:90%}
  #inviteModal textarea,#acceptModal textarea{width:100%;height:90px;background:#0d0f13;color:var(--txt);border:1px solid #2a2e38;border-radius:8px;padding:8px;font-size:12px}
  #inviteModal button,#acceptModal button{margin-top:10px;background:var(--acc);border:none;color:#fff;padding:7px 14px;border-radius:8px;cursor:pointer}
</style>
</head>
<body>
<header><span class="dot" id="livedot"></span><h1>SecureDM 加密私信</h1><span class="st" id="status">…</span></header>
<div id="compatBanner" style="background:#2a2410;border-bottom:1px solid #4a3f18;color:#e8d98a;font-size:12.5px;padding:6px 12px;display:flex;align-items:center;gap:10px">
  <span style="flex:1">⚠ 与 <b>SimpleX 官方客户端</b> 目前仅互通<b>文字消息</b>;语音/视频/屏幕共享/文件互传/群聊需在<b>本应用两端</b>之间进行。请对方也用本应用(分享本程序)。</span>
  <span onclick="document.getElementById('compatBanner').style.display='none'" style="cursor:pointer;color:#8a7c4a;padding:0 4px" title="关闭提示">✕</span>
</div>
<div id="main">
  <div id="contacts">
    <div class="hd"><b>联系人</b></div>
    <div class="hd2">
      <button onclick="newInvite()" title="生成邀请链接/二维码发给对方">+ 邀请</button>
      <button onclick="acceptInvite()" title="粘贴对方给你的链接建立联系">+ 添加</button>
      <button onclick="delContact()" title="删除选中的联系人" style="color:var(--bad)">🗑 删除</button>
      <button onclick="openSettings()" title="设置:自定义 ID / 聊天记录口令加密">⚙ 设置</button>
    </div>
    <div id="clist"></div>
  </div>
  <div id="chat">
    <div id="msgs"><div id="empty"><div>选择左侧联系人开始加密对话</div><div style="font-size:12px">+ 邀请 = 生成链接发给对方;+ 添加 = 粘贴对方给你的链接</div></div></div>
    <div id="composer">
      <select id="ttlSel" title="阅后即焚:到点双方客户端协议级自毁(防不住对方截图/拍照)" style="background:var(--panel);border:1px solid var(--line);color:var(--dim);border-radius:8px;padding:0 6px;font-size:12px">
        <option value="0">不过期</option>
        <option value="10">10秒焚</option>
        <option value="60">1分钟焚</option>
        <option value="300">5分钟焚</option>
        <option value="3600">1小时焚</option>
      </select>
      <input id="in" placeholder="发 E2E 加密消息…" disabled>
      <button id="send" onclick="sendMsg()">发送</button>
      <button id="attachBtn" title="发送文件" onclick="toggleAttach()">📎</button>
      <button id="callBtn" title="语音通话" onclick="startCall(false)">📞</button>
      <button id="videoBtn" title="视频通话" onclick="startCall(true)">📹</button>
    </div>
    <div id="callPanel" style="display:none;padding:10px 14px;border-top:1px solid var(--line);background:#14161c">
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <video id="remoteVideo" autoplay playsinline style="width:280px;background:#000;border-radius:8px"></video>
        <video id="localVideo" autoplay playsinline muted style="width:120px;background:#000;border-radius:8px"></video>
        <div style="display:flex;flex-direction:column;gap:7px">
          <div id="callStatus" style="font-size:13px;color:var(--dim)">呼叫中…</div>
          <div style="display:flex;gap:7px">
            <button onclick="toggleMic()" id="micBtn" style="background:#3a3f4b;border:none;color:#fff;padding:6px 12px;border-radius:7px;cursor:pointer">🎤 静音</button>
            <button onclick="toggleCam()" id="camBtn" style="background:#3a3f4b;border:none;color:#fff;padding:6px 12px;border-radius:7px;cursor:pointer">📷 关视频</button>
            <button onclick="switchCamera()" id="switchCamBtn" title="切换前后摄像头" style="background:#3a3f4b;border:none;color:#fff;padding:6px 12px;border-radius:7px;cursor:pointer">🔄 切换摄像头</button>
            <button onclick="shareScreen()" id="screenBtn" style="background:#3a3f4b;border:none;color:#fff;padding:6px 12px;border-radius:7px;cursor:pointer">🖥️ 共享屏幕</button>
            <button onclick="endCall()" style="background:var(--bad);border:none;color:#fff;padding:6px 12px;border-radius:7px;cursor:pointer">挂断</button>
          </div>
        </div>
      </div>
    </div>
    <div id="attach" style="display:none;padding:9px 16px;border-top:1px solid var(--line);gap:8px;align-items:center;flex-wrap:wrap">
      <button onclick="pickFile()" title="打开文件夹选择要发送的文件" style="background:var(--panel);border:1px solid var(--acc);color:var(--acc);padding:7px 12px;border-radius:8px;cursor:pointer">📁 选文件</button>
      <input id="fpath" placeholder="文件绝对路径(或点「选文件」)" style="flex:1;min-width:180px;background:var(--panel);border:1px solid #2a2e38;color:var(--txt);border-radius:8px;padding:8px 10px">
      <input type="file" id="filePickFallback" style="display:none" onchange="onFallbackPick(this)">
      <label title="勾选后自动建立签名信任并附签名清单,对方能看到「✓ 已验证来自 你」;无需再去联系人区点钥匙" style="font-size:13px;color:var(--dim);display:flex;align-items:center;gap:4px"><input type="checkbox" id="fsigned" checked>签名发送</label>
      <button onclick="sendFile()" style="background:var(--acc);border:none;color:#fff;padding:7px 14px;border-radius:8px;cursor:pointer">发文件</button>
      <div style="width:100%;display:flex;align-items:center;gap:6px;font-size:12px;color:var(--dim)">
        <span>下载到:</span><span id="dlDir" style="color:var(--txt)">…</span>
        <button onclick="pickDownloadDir()" title="自定义下载保存目录(桌面壳)" style="background:var(--panel);border:1px solid #2a2e38;color:var(--dim);padding:3px 9px;border-radius:6px;cursor:pointer;font-size:12px">改目录</button>
      </div>
    </div>
  </div>
</div>
<div id="inviteModal"><div class="box"><b id="inviteTitle">一次性邀请链接</b><div style="font-size:12px;color:var(--dim);margin:6px 0" id="inviteDesc">发给对方,在其 SimpleX/SecureDM 里"通过链接连接"接受即建立 E2E 联系</div><div style="display:flex;gap:14px;font-size:12.5px;color:var(--dim);margin-bottom:8px;flex-wrap:wrap"><label style="display:flex;align-items:center;gap:4px;cursor:pointer"><input type="radio" name="invKind" value="once" checked onchange="genInvite()">一次性(私聊)</label><label style="display:flex;align-items:center;gap:4px;cursor:pointer"><input type="radio" name="invKind" value="anon" onchange="genInvite()">匿名一次性</label><label style="display:flex;align-items:center;gap:4px;cursor:pointer"><input type="radio" name="invKind" value="long" onchange="genInvite()">长期有效(公共二维码)</label></div><textarea id="inviteLink" readonly></textarea><div style="display:flex;justify-content:center;margin:10px 0"><canvas id="inviteQr" width="240" height="240" style="background:#fff;border-radius:8px;padding:6px"></canvas></div><div id="inviteQrMsg" style="display:none;font-size:12px;color:var(--warn);text-align:center;margin-bottom:8px">链接过长,请复制粘贴</div><button onclick="copyInvite()">复制</button> <button onclick="closeInvite()">关闭</button></div></div>
<div id="acceptModal"><div class="box"><b>添加联系人</b><div style="font-size:12px;color:var(--dim);margin:6px 0">粘贴对方发给你的一次性邀请链接(对方点「+ 邀请」生成的)</div><textarea id="acceptLink" placeholder="粘贴邀请链接,例如 simplex://… 或 https://simplex.chat/invitation#…"></textarea><button id="acceptGoBtn" onclick="doAccept()">连接</button> <button onclick="closeAccept()" style="background:#3a3e48">取消</button></div></div>
<div id="settingsModal"><div class="box"><b>设置</b>
  <div style="margin:14px 0 6px;font-size:13px;color:var(--dim)">自定义 ID(显示名)</div>
  <div style="display:flex;gap:8px"><input id="idName" placeholder="你的显示名(对方看到)" style="flex:1;background:#0d0f13;border:1px solid #2a2e38;color:var(--txt);border-radius:8px;padding:8px;font-size:13px"><button onclick="saveIdentity()">保存</button></div>
  <div id="idMsg" style="font-size:12px;color:var(--dim);margin-top:4px"></div>
  <hr style="border:none;border-top:1px solid var(--line);margin:16px 0">
  <div style="margin:0 0 6px;font-size:13px;color:var(--dim)">聊天记录口令加密 <span id="dbState" style="color:var(--warn)"></span></div>
  <div style="font-size:12px;color:var(--dim);margin-bottom:8px">设口令后,聊天记录库以 SQLCipher 加密;每次启动需输入口令解锁,口令错进不去(同官方)。请务必记牢,丢失无法恢复。</div>
  <div id="dbSetRow" style="display:flex;flex-direction:column;gap:8px">
    <input id="dbPwd" type="password" placeholder="设置口令(≥4 位)" style="background:#0d0f13;border:1px solid #2a2e38;color:var(--txt);border-radius:8px;padding:8px;font-size:13px">
    <input id="dbPwd2" type="password" placeholder="再输一遍确认(防首次打错)" style="background:#0d0f13;border:1px solid #2a2e38;color:var(--txt);border-radius:8px;padding:8px;font-size:13px">
    <div><button onclick="setDbPassword()">设置口令</button></div>
  </div>
  <div id="dbUnlockRow" style="display:none;flex-direction:column;gap:8px">
    <div style="display:flex;gap:8px"><input id="dbPwdUnlock" type="password" placeholder="输入口令解锁" style="flex:1;background:#0d0f13;border:1px solid #2a2e38;color:var(--txt);border-radius:8px;padding:8px;font-size:13px"><button onclick="unlockDb()">解锁</button></div>
    <div style="font-size:12px;color:var(--dim)">忘记口令?<a href="javascript:void(0)" onclick="forgotPassword()" style="color:var(--warn)">新建用户ID</a>(原聊天记录将无法查看)</div>
    <hr style="border:none;border-top:1px solid var(--line);margin:10px 0">
    <div style="font-size:12px;color:var(--dim)">变更口令(需先验证旧口令):</div>
    <input id="dbPwdOld" type="password" placeholder="旧口令" style="background:#0d0f13;border:1px solid #2a2e38;color:var(--txt);border-radius:8px;padding:8px;font-size:13px">
    <input id="dbPwdNew" type="password" placeholder="新口令(≥4 位)" style="background:#0d0f13;border:1px solid #2a2e38;color:var(--txt);border-radius:8px;padding:8px;font-size:13px">
    <input id="dbPwdNew2" type="password" placeholder="再输一遍新口令" style="background:#0d0f13;border:1px solid #2a2e38;color:var(--txt);border-radius:8px;padding:8px;font-size:13px">
    <div><button onclick="changeDbPassword()">变更口令</button></div>
  </div>
  <div id="dbMsg" style="font-size:12px;color:var(--dim);margin-top:4px"></div>
  <div style="margin-top:16px;text-align:right"><button onclick="closeSettings()" style="background:#3a3e48">关闭</button></div>
</div></div>
<script>
const TOKEN=(()=>{const q=new URLSearchParams(location.search).get('token');if(q){localStorage.setItem('dm_token',q);history.replaceState({},'',location.pathname);return q;}return localStorage.getItem('dm_token')||'';})();
// WS server 地址由服务端注入(电脑 LAN IP,手机经 WiFi 可达;本机访问时服务端填 127.0.0.1)
const __WS_HOST__ = '__WS_HOST_VALUE__';
const H=(e)=>{const h=Object.assign({},e||{});if(TOKEN)h['X-L4-Token']=TOKEN;return h;};
let cur=null;
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function setSt(t,ok){document.getElementById('status').textContent=t;document.getElementById('livedot').style.background=ok===false?'var(--bad)':'var(--ok)';}
async function api(path,opts){const r=await fetch(path,Object.assign({headers:H({'Content-Type':'application/json'})},opts||{}));return r.json();}

async function refresh(){
  const s=await api('/dm/api/status');
  if(!s.ok){setSt('错误',false);return;}
  if(!s.output.running){setSt('未初始化 — 点击启动',false);
    document.getElementById('msgs').innerHTML='<div id="empty"><button style="background:var(--acc);border:none;color:#fff;padding:10px 20px;border-radius:9px;cursor:pointer" onclick="doSetup()">启动 SecureDM(初始化身份)</button></div>';return;}
  setSt((s.output.active_user||'')+' · 已连接',true);
  window._activeUser = s.output.active_user || 'me';  // WS 房间命名用(两端各自算 room)
  loadContacts();
  // 显示 WS 信令连接状态(便于诊断手机 WS 是否连上 VPS)
  setTimeout(()=>{
    const st=document.getElementById('status');
    if(st && typeof wsReady!=='undefined') st.textContent=(s.output.active_user||'')+(wsReady?' · 信令✓':' · 信令✗(检查网络)');
  },2500);
}
async function doSetup(){await api('/dm/api/setup',{method:'POST',body:'{}'});refresh();}
async function loadContacts(){
  // 来电模态优先级高于轮询刷新:响铃期间跳过联系人重渲染,避免任何重绘干扰来电 UI
  if(document.querySelector('.incomingCallModal'))return;
  const c=await api('/dm/api/contacts');
  const cl=document.getElementById('clist');cl.innerHTML='';
  if(!c.ok||!c.output.length){cl.innerHTML='<div style="padding:14px;color:var(--dim);font-size:13px">暂无联系人<br>点 + 邀请 开始</div>';return;}
  c.output.forEach(ct=>{
    const d=document.createElement('div');d.className='citem'+(cur&&cur.contact_id===ct.contact_id?' active':'');
    d.innerHTML='<div class="nm">'+esc(ct.display_name||('联系人'+ct.contact_id))+'</div><div class="pv">'+esc(ct.preview||'')+'</div>';
    d.onclick=()=>openChat(ct);cl.appendChild(d);
  });
}
// 已渲染消息的索引:itemId -> DOM 节点。reconcile 只增不删,防 5s 轮询闪烁。
const _rendered = new Map();
let _optimisticSeq = 0;

function _msgNode(m){
  if(m.kind==='manifest'){
    const d=document.createElement('div');d.className='m manifest';
    d.textContent='[签名清单] '+m.text.slice(0,80)+'…';return d;
  }
  const d=document.createElement('div');
  d.className='m '+(m.dir==='me'?'me':'them');
  d.textContent=m.text;return d;
}

async function openChat(ct, isRefresh){
  // 响铃期间不轮询重渲染聊天区(用户手动点击仍允许:此时无来电模态或用户已明确要切换)
  if(isRefresh && document.querySelector('.incomingCallModal'))return;
  cur=ct;loadContacts();
  document.getElementById('in').disabled=false;
  let r;
  try{ r=await api('/dm/api/history?contact='+encodeURIComponent(ct.contact_id)); }
  catch(e){ if(!isRefresh){const box=document.getElementById('msgs');box.innerHTML='<div class="sys">加载失败:'+esc(e)+'</div>';} return; }
  if(!r.ok){
    if(!isRefresh){const box=document.getElementById('msgs');box.innerHTML='<div class="sys">'+esc(r.error||'加载失败')+'</div>';}
    return;
  }
  const box=document.getElementById('msgs');
  const nearBottom = (box.scrollHeight - box.scrollTop - box.clientHeight) < 120;
  // 首次打开:清空重建;之后 reconcile(只追加新消息,不重排不闪烁)
  if(!isRefresh){ box.innerHTML=''; _rendered.clear(); }
  (r.output.messages||[]).forEach(m=>{
    // 1) 服务端去重:同一 itemId 不重复渲染
    if(m.id!=null && _rendered.has('srv_'+m.id)) return;
    // 2) 乐观气泡 reconcile:同方向+全文本匹配的乐观气泡,原地转正(不新增不跳侧)
    const optKey = 'txt_'+m.dir+'_'+m.text;
    const existing = _rendered.get(optKey);
    if(existing){
      // 已渲染过同文本气泡(乐观插入或上次),标记为已确认并跳过
      if(m.id!=null){ _rendered.set('srv_'+m.id, existing); }
      return;
    }
    const node=_msgNode(m);
    node.dataset.key = m.id!=null ? ('srv_'+m.id) : optKey;
    box.appendChild(node);
    if(m.id!=null) _rendered.set('srv_'+m.id, node);
    _rendered.set(optKey, node);
  });
  // 待下载文件
  (r.output.incoming_files||[]).forEach(f=>{
    const fk='file_'+f.file_id;
    if(_rendered.has(fk))return;
    const card=fileCard(ct,f);card.dataset.key=fk;box.appendChild(card);_rendered.set(fk,card);
  });
  if(nearBottom||!isRefresh) box.scrollTop=box.scrollHeight;
}
function fileCard(ct,f){
  const d=document.createElement('div');d.className='fmsg';
  d.innerHTML='<div class="fname">📎 '+esc(f.file_name||'文件')+'</div>';
  const vb=document.createElement('span');vb.className='vbadge pending';vb.textContent='…';d.appendChild(vb);
  const btn=document.createElement('button');btn.textContent='下载';d.appendChild(btn);
  btn.onclick=async()=>{
    btn.disabled=true;btn.textContent='下载中…';
    const rr=await api('/dm/api/receive_file',{method:'POST',body:JSON.stringify({file_id:f.file_id})});
    btn.textContent=rr.ok?'已下载':'失败';
    // 显示可见保存路径(自定义目录或默认目录),让用户知道文件在哪
    if(rr.ok&&rr.output&&(rr.output.display_path||rr.output.saved_path)){
      const loc=document.createElement('div');
      loc.style.cssText='font-size:11.5px;color:var(--dim);margin-top:4px;word-break:break-all';
      loc.textContent='已存到 '+(rr.output.display_path||rr.output.saved_path);
      d.appendChild(loc);
    }
    verifyBadge(ct,f.file_name,vb);
  };
  verifyBadge(ct,f.file_name,vb);
  return d;
}
async function verifyBadge(ct,fname,el){
  el.className='vbadge pending';el.textContent='校验中…';
  const v=await api('/dm/api/verify_file?contact='+encodeURIComponent(ct.contact_id)+'&file_name='+encodeURIComponent(fname));
  if(v.ok&&v.output&&v.output.verified){el.className='vbadge ok';el.textContent='✓ 已验证来自 '+(v.output.sender||'对方');}
  else if(v.ok&&v.output){el.className='vbadge bad';el.textContent='✗ 校验失败';el.title=v.diagnosable||'';}
  else{el.className='vbadge pending';el.textContent='无签名';el.title=(v.diagnosable||v.error||'');}
}
async function sendMsg(){
  const inp=document.getElementById('in');const t=inp.value.trim();if(!t||!cur)return;
  inp.value='';
  const box=document.getElementById('msgs');
  // 乐观插入:最终位置/样式,用全文本 key 登记;服务端同文本到达时原地转正(不重复不跳侧)
  const d=document.createElement('div');d.className='m me';d.textContent=t;
  _rendered.set('txt_me_'+t, d);
  box.appendChild(d);box.scrollTop=box.scrollHeight;
  const r=await api('/dm/api/send',{method:'POST',body:JSON.stringify({contact:String(cur.contact_id),text:t})});
  if(!r.ok){
    _rendered.delete('txt_me_'+t);
    d.remove();
    const e=document.createElement('div');e.className='sys';e.textContent='发送失败:'+(r.error||'');box.appendChild(e);
  }
}
function toggleAttach(){const a=document.getElementById('attach');a.style.display=a.style.display==='none'?'flex':'none';if(a.style.display==='flex')loadDlDir();}
async function loadDlDir(){
  const r=await api('/dm/api/get_download_dir',{method:'POST',body:'{}'});
  if(r.ok&&r.output)document.getElementById('dlDir').textContent=r.output.effective||r.output.default||'(未知)';
}
async function pickDownloadDir(){
  const T=window.__TAURI__;
  if(!(T&&T.core&&T.core.invoke)){alert('自定义下载目录需要桌面壳的原生对话框。\n当前下载目录见上方显示。');return;}
  try{
    const p=await T.core.invoke('pick_folder');
    if(!p)return; // 取消
    const r=await api('/dm/api/set_download_dir',{method:'POST',body:JSON.stringify({path:p})});
    if(r.ok){loadDlDir();}else{alert('设置失败:'+(r.error||r.diagnosable||''));}
  }catch(e){alert('选择目录调用失败:'+(e&&e.message?e.message:e));}
}
// 选文件:壳内走原生「打开文件」对话框拿绝对路径(SimpleX 按路径读盘);
// 浏览器降级为 <input type=file>(浏览器只能给 File 对象,拿不到绝对路径 → 提示)。
async function pickFile(){
  // 必须走桌面壳的原生对话框 —— SimpleX daemon 按绝对路径读盘,
  // 浏览器/远程页拿不到绝对路径,故没有原生桥时直接报错,不再静默降级误导。
  const T=window.__TAURI__;
  if(T&&T.core&&T.core.invoke){
    try{
      const p=await T.core.invoke('pick_file');
      if(p){document.getElementById('fpath').value=p;}
      return; // 取消则不动
    }catch(e){
      alert('选文件调用失败:'+(e&&e.message?e.message:e));return;
    }
  }
  alert('「选文件」需要桌面壳的原生对话框。\n当前页未检测到壳桥(window.__TAURI__)。\n请用桌面壳打开,或手动把文件绝对路径填进输入框。');
}
function onFallbackPick(inp){
  const f=inp.files&&inp.files[0];if(!f)return;
  alert('浏览器无法获取文件完整路径。\n请改用桌面壳(📁 选文件),或手动把文件绝对路径填进输入框。\n已选:'+f.name);
  inp.value='';
}
async function sendFile(){
  if(!cur)return;
  const path=document.getElementById('fpath').value.trim();if(!path)return;
  const signed=document.getElementById('fsigned').checked;
  const box=document.getElementById('msgs');
  const note=document.createElement('div');note.className='sys';note.textContent='发送文件…';box.appendChild(note);box.scrollTop=box.scrollHeight;
  const r=await api('/dm/api/send_file',{method:'POST',body:JSON.stringify({contact:String(cur.contact_id),path,signed})});
  note.remove();
  const d=document.createElement('div');d.className='fmsg';
  if(r.ok){d.innerHTML='<div class="fname">📎 已发送 '+(r.output.file||path.split(/[\\/]/).pop())+(signed?' <span class="vbadge ok">带签名</span>':'')+'</div>';}
  else{d.innerHTML='<div class="fname" style="color:var(--bad)">发送失败:'+esc(r.error||'')+'</div><div style="font-size:12px;color:var(--dim)">'+esc(r.diagnosable||'')+'</div>';}
  box.appendChild(d);box.scrollTop=box.scrollHeight;
  document.getElementById('fpath').value='';
}
function newInvite(){document.getElementById('inviteModal').style.display='flex';genInvite();}
// ── 设置:自定义 ID + 聊天记录口令 ─────────────────────────────
async function openSettings(){
  document.getElementById('settingsModal').style.display='flex';
  document.getElementById('idMsg').textContent='';document.getElementById('dbMsg').textContent='';
  const idr=await api('/dm/api/get_identity',{method:'POST',body:'{}'});
  if(idr.ok)document.getElementById('idName').value=idr.output.display_name||'';
  const pr=await api('/dm/api/db_password_status',{method:'POST',body:'{}'});
  const enc=pr.ok&&pr.output.encrypted;
  document.getElementById('dbState').textContent=enc?'(已设口令)':'(未加密)';
  document.getElementById('dbSetRow').style.display=enc?'none':'flex';
  document.getElementById('dbUnlockRow').style.display=enc?'flex':'none';
}
function closeSettings(){document.getElementById('settingsModal').style.display='none';}
async function saveIdentity(){
  const name=document.getElementById('idName').value.trim();
  const m=document.getElementById('idMsg');
  if(!name){m.textContent='填一个显示名';return;}
  const r=await api('/dm/api/set_identity',{method:'POST',body:JSON.stringify({name})});
  m.style.color=r.ok?'var(--ok)':'var(--bad)';
  m.textContent=r.ok?('已改为「'+name+'」(对方刷新后可见)'):('失败:'+(r.error||'')+(r.diagnosable||''));
}
async function setDbPassword(){
  const pwd=document.getElementById('dbPwd').value;
  const pwd2=document.getElementById('dbPwd2').value;
  const m=document.getElementById('dbMsg');
  if(pwd.length<4){m.style.color='var(--bad)';m.textContent='口令至少 4 位';return;}
  if(pwd!==pwd2){m.style.color='var(--bad)';m.textContent='两次输入不一致,请重输(防首次打错锁死)';return;}
  const r=await api('/dm/api/db_set_password',{method:'POST',body:JSON.stringify({password:pwd})});
  m.style.color=r.ok?'var(--ok)':'var(--bad)';
  m.textContent=r.ok?(r.diagnosable||'已设置'):('失败:'+(r.error||'')+(r.diagnosable||''));
  if(r.ok){document.getElementById('dbState').textContent='(已设口令)';document.getElementById('dbSetRow').style.display='none';}
}
async function unlockDb(){
  const pwd=document.getElementById('dbPwdUnlock').value;
  const m=document.getElementById('dbMsg');
  const r=await api('/dm/api/db_unlock',{method:'POST',body:JSON.stringify({password:pwd})});
  m.style.color=r.ok?'var(--ok)':'var(--bad)';
  m.textContent=r.ok?'✓ 口令正确,已解锁':'✗ '+(r.error||'口令错误');
}
// 变更口令:先验证旧口令,再把新口令派生的密钥写回密钥文件。
// 注意:这改的是「派生密钥」,真正的 SQLCipher 库 rekey 需底层 sqlcipher_export
// 迁移(后续);当前对新派生密钥生效,与「首次设口令」同一条路径。
async function changeDbPassword(){
  const oldp=document.getElementById('dbPwdOld').value;
  const np=document.getElementById('dbPwdNew').value;
  const np2=document.getElementById('dbPwdNew2').value;
  const m=document.getElementById('dbMsg');
  if(np.length<4){m.style.color='var(--bad)';m.textContent='新口令至少 4 位';return;}
  if(np!==np2){m.style.color='var(--bad)';m.textContent='两次新口令不一致,请重输';return;}
  const r=await api('/dm/api/db_change_password',{method:'POST',body:JSON.stringify({old_password:oldp,new_password:np})});
  m.style.color=r.ok?'var(--ok)':'var(--bad)';
  m.textContent=r.ok?(r.diagnosable||'口令已变更'):('失败:'+(r.error||'')+(r.diagnosable||''));
  if(r.ok){document.getElementById('dbPwdOld').value='';document.getElementById('dbPwdNew').value='';document.getElementById('dbPwdNew2').value='';}
}
// 忘记口令 → 新建用户ID:旧加密库打不开(记录看不了),以新身份从头开始
async function forgotPassword(){
  if(!confirm('新建用户ID将放弃当前加密的聊天记录(无法查看),并以全新身份重新开始。\n\n确定继续?'))return;
  if(!confirm('再次确认:原聊天记录将无法恢复。仍要新建用户ID?'))return;
  const m=document.getElementById('dbMsg');
  const r=await api('/dm/api/new_user_id',{method:'POST',body:'{}'});
  m.style.color=r.ok?'var(--ok)':'var(--bad)';
  m.textContent=r.ok?('已新建用户ID,正在重启…'):('失败:'+(r.error||'')+(r.diagnosable||''));
  if(r.ok)setTimeout(()=>{closeSettings();refresh();},1500);
}

async function genInvite(){
  const kind=(document.querySelector('input[name=invKind]:checked')||{}).value||'once';
  const ep={'once':'/dm/api/create_invite','anon':'/dm/api/create_invite_incognito','long':'/dm/api/create_address'}[kind];
  const title={'once':'一次性邀请链接','anon':'匿名一次性邀请','long':'长期有效地址(公共二维码)'}[kind];
  const desc={'once':'发给对方,在其 SimpleX/SecureDM 里"通过链接连接"接受即建立 E2E 联系','anon':'对方看到的是随机临时档案,不是你的显示名;一次性','long':'可无限次扫码添加,适合公共场合张贴;泄露需删除地址(官方客户端)'}[kind];
  document.getElementById('inviteTitle').textContent=title;
  document.getElementById('inviteDesc').textContent=desc;
  const ta=document.getElementById('inviteLink');ta.value='生成中…';
  const r=await api(ep,{method:'POST',body:'{}'});
  if(r.ok&&r.output){const link=(typeof r.output==='string'?r.output:(r.link||r.output.link||''));ta.value=link;renderInviteQr(link);}
  else{ta.value='';alert('生成邀请失败:'+(r.diagnosable||r.error||'未知错误'));}
}
// 粘贴对方给的邀请链接 → 建立联系(对应对方的 + 邀请)
function acceptInvite(){document.getElementById('acceptLink').value='';document.getElementById('acceptModal').style.display='flex';}
function closeAccept(){document.getElementById('acceptModal').style.display='none';}
async function doAccept(){
  const link=document.getElementById('acceptLink').value.trim();
  if(!link){alert('先粘贴邀请链接');return;}
  const btn=document.getElementById('acceptGoBtn');btn.disabled=true;btn.textContent='连接中…';
  try{
    const r=await api('/dm/api/accept_invite',{method:'POST',body:JSON.stringify({link})});
    if(r.ok){closeAccept();alert('已建立联系');loadContacts();}
    else{alert('添加失败:'+(r.diagnosable||r.error||'未知错误'));}
  }finally{btn.disabled=false;btn.textContent='连接';}
}
// 删除当前选中的联系人(二次确认,防止误删)
async function delContact(){
  if(!cur){alert('先在左侧选中一个联系人');return;}
  if(!confirm('确定删除联系人「'+cur.display_name+'」?\n此操作不可恢复,对话历史将一并清除。'))return;
  const r=await api('/dm/api/delete_contact',{method:'POST',body:JSON.stringify({contact:String(cur.contact_id)})});
  if(r.ok){cur=null;loadContacts();document.getElementById('msgs').innerHTML='<div id="empty"><div>联系人已删除</div></div>';}
  else{alert('删除失败:'+(r.diagnosable||r.error||'未知错误'));}
}
// A2H 审批卡:轮询待裁决请求,渲染确认/取消按钮(L4 唯一交互=确认/追问,架构 §7.4)
async function pollA2H(){
  try{
    const r=await api('/dm/api/a2h_status');
    if(!r.ok)return;
    renderA2H(r.output.pending||[]);
  }catch(e){}
}
function renderA2H(pending){
  // 移除旧审批卡
  document.querySelectorAll('.a2hcard').forEach(e=>e.remove());
  const box=document.getElementById('msgs');
  pending.forEach(p=>{
    const d=document.createElement('div');d.className='fmsg a2hcard';d.style.borderColor='var(--warn)';
    d.innerHTML='<div class="fname" style="color:var(--warn)">⚠ agent 请求审批</div><div style="font-size:13px;margin:5px 0">'+esc(p.action||'')+'</div><div style="font-size:12px;color:var(--dim)">'+esc(p.reason||'')+'</div>';
    const row=document.createElement('div');row.style.marginTop='8px';
    const yes=document.createElement('button');yes.textContent='批准';yes.style.cssText='background:var(--ok);border:none;color:#fff;padding:6px 14px;border-radius:7px;cursor:pointer;margin-right:8px';
    const no=document.createElement('button');no.textContent='拒绝';no.style.cssText='background:var(--bad);border:none;color:#fff;padding:6px 14px;border-radius:7px;cursor:pointer';
    yes.onclick=()=>{sendText('yes '+p.request_id);d.remove();};
    no.onclick=()=>{sendText('no '+p.request_id);d.remove();};
    row.appendChild(yes);row.appendChild(no);d.appendChild(row);
    box.appendChild(d);
  });
  box.scrollTop=box.scrollHeight;
}
async function sendText(t){
  if(!cur)return;
  const ttl=parseInt((document.getElementById('ttlSel')||{}).value||'0',10)||0;
  await api('/dm/api/send',{method:'POST',body:JSON.stringify({contact:String(cur.contact_id),text:t,ttl})});
}

// ═══════════════ 2 人 E2E 通话(P2P WebRTC,信令经 E2E 加密通道)═══════════════
let pc=null, localStream=null, callState='idle', callTimer=null, screenTrack=null, isCaller=false, callStartTs=0;
let _makingOffer=false;  // 完美协商:本端正在产 reoffer 时置位,供 glare 回退判定
const ICE_SERVERS=[{urls:'stun:stun.l.google.com:19302'}];  // P2P STUN;内网直连为主,无需 TURN
const PAGE_LOAD_TS = new Date().toISOString();  // 只响应页面加载后的信令,忽略历史遗留
const STALE_CALL_MS = 45000;  // 通话握手 45s 未完成自动复位 idle(防卡在 answering 收 busy 死循环)

function setCallStatus(t){const el=document.getElementById('callStatus');if(el)el.textContent=t;}
// [临时诊断] 共享链路打点:同时进 console + 状态条(便于壳内直接看),定位断在哪一环
function _dbg(tag,obj){const s='[屏诊] '+tag+(obj!==undefined?(' '+JSON.stringify(obj)):'');try{console.log(s);}catch(e){} setCallStatus(s);}
function showCallPanel(show){document.getElementById('callPanel').style.display=show?'block':'none';}

// 卡死看门狗:非 idle 但长时间未 connected,强制复位
setInterval(()=>{
  if(callState!=='idle' && callState!=='connected' && (Date.now()-callStartTs)>STALE_CALL_MS){
    endCall(true);  // 静默复位
  }
},5000);

// ═══ WebSocket 信令(方案 A):实时推送,不轮询聊天记录 ═══
// 两端浏览器各连 VPS 上的 WS 信令服务器(wss://signal.dreamproject.qzz.io/ws,LE 证书),
// SDP/ICE 经服务器**实时转发**给对端。房间 = 双方身份排序拼接(两端算出同一 room)。
let wsSig=null, wsReady=false, myPeerId=null, currentRoom=null, _wsManualClose=false;

// WS 信令服务器(VPS,Let's Encrypt 证书,公网可达,手机/电脑直连,不依赖 adb/局域网)
const WS_SIGNAL_URL = 'wss://signal.dreamproject.qzz.io/ws';
function _wsUrl(){ return WS_SIGNAL_URL; }
// 房间模型:每人一个"个人收件房间"(以**对方**视角命名)。主叫向对方的收件房间发 offer。
// 计算规则:room = 'inbox-' + 目标身份的**服务端 userId 等价名**。两端都用 display_name 小写,保证一致。
function _inboxRoom(identity){ return 'inbox-'+(identity||'me').toLowerCase(); }
function _callRoom(a, b){ return 'call-'+[a.toLowerCase(), b.toLowerCase()].sort().join('__'); }

function ensureWs(onready){
  if(wsSig && wsReady){ if(onready)onready(); return; }
  if(wsSig && wsSig.readyState===WebSocket.CONNECTING){ if(onready)wsSig.addEventListener('open',()=>onready(),{once:true}); return; }
  wsSig = new WebSocket(_wsUrl());
  wsSig.onopen = ()=>{
    wsReady=true;
    myPeerId = (window._activeUser||('peer-'+Math.random().toString(36).slice(2,8)));
    // 心跳保活(防 Chrome 空闲/网络设备掐断空闲 WS)
    if(window._wsPing)clearInterval(window._wsPing);
    window._wsPing=setInterval(()=>{ if(wsSig&&wsReady)wsSig.send(JSON.stringify({type:'ping'})); },25000);
    if(onready)onready();
  };
  wsSig.onmessage = (ev)=>{
    let msg; try{ msg=JSON.parse(ev.data); }catch(e){ return; }
    if(msg.type==='signal' && msg.data){ handleSignal(msg.data); }
    else if(msg.type==='peer-joined'){ setCallStatus('对方已上线'); }
    else if(msg.type==='peer-left'){ if(callState==='connected')endCall(true); }
  };
  wsSig.onclose = ()=>{
    wsReady=false; wsSig=null;
    // 自动重连(手机 Chrome 后台/网络抖动会断 WS,不重连就收不到来电)
    if(!_wsManualClose){ setTimeout(()=>{ ensureWs(()=>{ wsJoin(_inboxRoom(window._activeUser||'me'), myPeerId, false); if(currentRoom)wsJoin(currentRoom,myPeerId, true); }); }, 2000); }
  };
  wsSig.onerror = ()=>{ wsReady=false; };
}

// isCallRoom:仅当加入的是"通话房间"时才更新 currentRoom。
// "个人收件房间"(_inboxRoom)只是收 offer 的邮箱,不是通话房间,join 它绝不能污染 currentRoom
// (否则断线重连后 sendSig 的 answer/ice/end 会默认发到收件房间,对方收不到,通话静默坏死)。
function wsJoin(room, peer, isCallRoom){
  if(wsSig && wsReady){
    if(isCallRoom===true) currentRoom = room;
    wsSig.send(JSON.stringify({type:'join', room, peer, token: TOKEN}));
  }
}

function sendSig(sig, room){
  // 经 WS 实时发送(替代旧的 simplex_send_message 轮询通道)。
  // 默认发当前通话房间;offer 由主叫发到对方的收件房间。
  const r = room || currentRoom;
  if(wsSig && wsReady && r){
    wsSig.send(JSON.stringify({type:'signal', room: r, data: sig}));
  }
}

async function getMedia(video){
  // 显式开 AEC/降噪/AGC(复用 _audioConstraints):手机外放场景的声学回声自激,
  // 裸 audio:true 在安卓 Chrome 常不打 AEC,导致环路增益>1 啸叫。
  const audioC = _audioConstraints();
  const constraints = video
    ? {audio: audioC, video:{width:{ideal:640},frameRate:{ideal:24},facingMode:{ideal:_facingMode}}}
    : {audio: audioC};
  return await navigator.mediaDevices.getUserMedia(constraints);
}

// 当前摄像头朝向:environment=后置(默认,性能更好),user=前置。手机端切换摄像头用。
let _facingMode = 'environment';

// 通话中无缝切换前后摄像头:重新采集对应朝向的视频轨,用 replaceTrack 替换
// (不重建 PeerConnection,对端无感知、不断连),并更新本地预览。
async function switchCamera(){
  if(!localStream || !localStream.getVideoTracks().length){ setCallStatus('当前无视频轨可切换'); return; }
  _facingMode = (_facingMode==='environment') ? 'user' : 'environment';
  // 手机摄像头是独占资源:必须先停掉旧轨释放摄像头,再采新轨,否则新采集 NotReadableError。
  const old = localStream.getVideoTracks()[0];
  try{
    if(old){ old.stop(); localStream.removeTrack(old); }   // 先释放旧摄像头
    const ns = await navigator.mediaDevices.getUserMedia({
      video:{width:{ideal:640},frameRate:{ideal:24},facingMode:{ideal:_facingMode}},
      audio:false,
    });
    const newTrack = ns.getVideoTracks()[0];
    if(!newTrack){ setCallStatus('未拿到新摄像头轨'); return; }
    // 替换发送给对端的轨(所有 video sender)
    if(pc){
      const senders = pc.getSenders().filter(s=>s.track && s.track.kind==='video');
      for(const s of senders){ try{ await s.replaceTrack(newTrack); }catch(e){} }
    }
    // 挂新轨并更新预览
    localStream.addTrack(newTrack);
    document.getElementById('localVideo').srcObject = localStream;
    setCallStatus('已切换到'+(_facingMode==='environment'?'后置':'前置')+'摄像头');
  }catch(e){
    // 失败时尝试恢复原朝向,避免视频轨彻底丢失
    setCallStatus('切换摄像头失败:'+e);
    try{
      _facingMode = (_facingMode==='environment') ? 'user' : 'environment';  // 回退朝向
      const rs = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:_facingMode}},audio:false});
      const rt = rs.getVideoTracks()[0];
      if(rt && pc){ for(const s of pc.getSenders().filter(s=>s.track&&s.track.kind==='video')){ try{ await s.replaceTrack(rt); }catch(e){} } localStream.addTrack(rt); document.getElementById('localVideo').srcObject=localStream; }
    }catch(e2){}
  }
}

async function startCall(video){
  if(!cur){alert('先选中联系人');return;}
  if(callState!=='idle'){alert('已在通话中');return;}
  isCaller=true;callState='calling';callStartTs=Date.now();
  showCallPanel(true);setCallStatus(video?'视频呼叫中…':'语音呼叫中…');
  try{
    localStream=await getMediaSafe(video);
    document.getElementById('localVideo').srcObject=localStream;
    setupPeer();
    applyLocalTracks();   // 挂本地轨 + 显式 sendrecv(替代旧 addTrack forEach)
    // 加入 WS:先 join 我的收件房间(听来电),再在通话房间发 offer
    const me = window._activeUser || 'me';
    // 信令房间身份源 = 对方跨端稳定的 profile 显示名(被叫监听房间 = 它自己的 profile 名,
    // 两端自动同源)。本地备注名(display_name)仅用于 UI 显示,不作房间路由。
    const peerName = cur.peer_profile_name || cur.display_name;
    await new Promise(res=>ensureWs(res));
    currentRoom = _callRoom(me, peerName);      // 通话房间(两端同名)
    wsJoin(currentRoom, myPeerId, true);       // 加入通话房间(收发 answer/ice/end)
    const offer=await pc.createOffer();
    await pc.setLocalDescription(offer);
    // 等 ICE gathering 基本完成后一次性发 offer(含 candidates)
    await waitIce();
    // offer 发到**对方的收件房间**(对方在那里监听来电)
    sendSig({type:'offer', sdp:pc.localDescription.sdp, video, from: me}, _inboxRoom(peerName));
    setCallStatus('等待对方接听…');
  }catch(e){setCallStatus('呼叫失败:'+e);callState='idle';}
}

// ── 接收端屏幕共享大屏:可拖动标题栏 + 右下角缩放手柄 + 轨道结束自动清理 ──
function _removeScreenView(){
  const w=document.getElementById('screenViewWrap');
  if(w)w.remove();
}
function _showScreenView(ev){
  let wrap=document.getElementById('screenViewWrap');
  if(!wrap){
    wrap=document.createElement('div');wrap.id='screenViewWrap';
    wrap.style.cssText='position:fixed;top:6%;left:50%;transform:translateX(-50%);width:64vw;height:70vh;background:#000;border:2px solid var(--acc);border-radius:10px;z-index:1000;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,.6)';
    // 标题栏(可拖动)
    const bar=document.createElement('div');
    bar.style.cssText='height:32px;line-height:32px;background:rgba(255,255,255,.08);color:#fff;font-size:13px;padding:0 10px;cursor:move;display:flex;justify-content:space-between;align-items:center;user-select:none';
    bar.innerHTML='<span>🖥️ 对方屏幕共享(拖动此栏移动,右下角缩放)</span>';
    const closeB=document.createElement('button');closeB.textContent='✕ 关闭';
    closeB.style.cssText='background:var(--bad);border:none;color:#fff;padding:3px 12px;border-radius:6px;cursor:pointer;font-size:12px';
    closeB.onclick=()=>_removeScreenView();
    bar.appendChild(closeB);
    wrap.appendChild(bar);
    // 视频
    const sv=document.createElement('video');sv.id='screenVideo';sv.autoplay=true;sv.playsInline=true;sv.muted=true;
    sv.style.cssText='width:100%;height:calc(100% - 32px);object-fit:contain;background:#000';
    wrap.appendChild(sv);
    // 右下角缩放手柄
    const grip=document.createElement('div');
    grip.style.cssText='position:absolute;right:0;bottom:0;width:20px;height:20px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,var(--acc) 50%);border-bottom-right-radius:8px';
    wrap.appendChild(grip);
    document.body.appendChild(wrap);
    // 拖动逻辑
    bar.addEventListener('mousedown',e=>{
      if(e.target===closeB)return;
      const r=wrap.getBoundingClientRect(),ox=e.clientX-r.left,oy=e.clientY-r.top;
      wrap.style.transform='none';wrap.style.left=r.left+'px';wrap.style.top=r.top+'px';
      const mv=ev2=>{wrap.style.left=(ev2.clientX-ox)+'px';wrap.style.top=(ev2.clientY-oy)+'px';};
      const up=()=>{document.removeEventListener('mousemove',mv);document.removeEventListener('mouseup',up);};
      document.addEventListener('mousemove',mv);document.addEventListener('mouseup',up);
    });
    // 缩放逻辑
    grip.addEventListener('mousedown',e=>{
      e.preventDefault();
      const r=wrap.getBoundingClientRect(),sw=r.width,sh=r.height,sx=e.clientX,sy=e.clientY;
      const mv=ev2=>{wrap.style.width=Math.max(280,sw+ev2.clientX-sx)+'px';wrap.style.height=Math.max(200,sh+ev2.clientY-sy)+'px';};
      const up=()=>{document.removeEventListener('mousemove',mv);document.removeEventListener('mouseup',up);};
      document.addEventListener('mousemove',mv);document.addEventListener('mouseup',up);
    });
  }
  const sv=wrap.querySelector('#screenVideo');
  if(ev.streams&&ev.streams[0])sv.srcObject=ev.streams[0];
  else{let s=new MediaStream();s.addTrack(ev.track);sv.srcObject=s;}
  // 兜底:对方停止共享(轨道 ended)时自动清大屏(双保险,主路径是 screenstop 信令)
  ev.track.onended=()=>_removeScreenView();
  sv.play().catch(()=>{});
}

function setupPeer(){
  pc=new RTCPeerConnection({iceServers:ICE_SERVERS});
  // 不预建 transceiver 再用 addTrack 填(易错:addTrack 复用 recvonly transceiver 不会提升方向,
  // 导致应答方 answer 被压成 recvonly 只收不发——probe 实测 audio=recvonly 证实)。
  // 改为:addTrack 让浏览器自然建 sendrecv transceiver;仅当本地确实缺某媒体时,才补一个 recvonly
  // 占位,保证仍能收到对方该媒体。方向提升统一在 applyLocalTracks 里做。
  pc.ontrack=(ev)=>{
    // 远端媒体:音频轨进专用 audio 元素;视频轨区分"摄像头"与"屏幕共享"(第二条视频轨)。
    let ra=document.getElementById('remoteAudio');
    if(!ra){ ra=document.createElement('audio');ra.id='remoteAudio';ra.autoplay=true;document.body.appendChild(ra); }
    if(ev.track.kind==='audio'){
      let s = ra.srcObject instanceof MediaStream ? ra.srcObject : new MediaStream();
      if(!s.getAudioTracks().includes(ev.track))s.addTrack(ev.track);
      ra.srcObject=s;ra.muted=false;ra.play().catch(()=>{});
      return;
    }
    // 视频轨:判断是摄像头还是屏幕共享。
    // 屏幕共享两条路径:① 对方 addTrack 新增第二条视频轨(contentHint=detail 或 alreadyHasCam);
    // ② 纯语音通话对方 replaceTrack 复用空闲 video transceiver(contentHint 不传,但本地没摄像头在发)。
    const rv=document.getElementById('remoteVideo');
    const alreadyHasCam = rv.srcObject && rv.srcObject.getVideoTracks().length>0;
    // 本地是否有正在发送的摄像头视频轨(有 → 这条新来的更可能是对方摄像头;没有 → 基本是对方屏幕)
    const localHasVideoSend = pc.getSenders().some(s=>s.track && s.track.kind==='video' && s.track!==screenTrack);
    const isScreen = ev.track.contentHint==='detail' || ev.track.contentHint==='text' || alreadyHasCam || !localHasVideoSend;
    _dbg('ontrack-video',{hint:ev.track.contentHint||'', alreadyCam:!!alreadyHasCam, localVidSend:localHasVideoSend, isScreen:isScreen});
    if(isScreen){
      // 屏幕共享:挂到可拖动/可缩放的大屏,并监听轨道结束自动清理
      _showScreenView(ev);
    }else{
      // 摄像头:挂到 remoteVideo(静音防回声,声音走 remoteAudio)
      if(ev.streams&&ev.streams[0])rv.srcObject=ev.streams[0];
      else{let s=new MediaStream();s.addTrack(ev.track);rv.srcObject=s;}
      rv.muted=true;
      rv.play().catch(()=>{});
    }
  };
  pc.onicecandidate=(ev)=>{ if(ev.candidate) sendSig({type:'ice', candidate:ev.candidate.toJSON()}); };
  // ── 重协商(完美协商 perfect negotiation)────────────────────────────
  // 为什么需要:通话建立后 shareScreen() addTrack 屏幕轨,浏览器只触发
  // negotiationneeded,不会自动重发 offer——没有这里,新轨永远不进 SDP,
  // 远端 ontrack 收不到屏幕轨,接收端大屏永不出现(2026-08-03 实测失败的根因)。
  // 完美协商防 glare(两端同时点共享→同时 offer 冲突):
  //   polite  = 被叫(!isCaller),收到对方 reoffer 时回退自己的 offer 接受对方;
  //   impolite= 主叫( isCaller),无视对方 reoffer,等对方接受自己的。
  pc.onnegotiationneeded=async()=>{
    _dbg('nego-need',{senders:pc.getSenders().length, ice:pc.iceGatheringState});
    try{
      _makingOffer=true;
      await pc.setLocalDescription(await pc.createOffer());
      await waitIce();
      sendSig({type:'reoffer', sdp:pc.localDescription.sdp});
      _dbg('reoffer-sent',{videoM:(pc.localDescription.sdp.match(/m=video/g)||[]).length});
    }catch(e){try{console.log('[屏诊] reoffer-fail(建联期 glare,常可忽略)',e);}catch(_){}}
    finally{_makingOffer=false;}
  };
  pc.onconnectionstatechange=()=>{
    setCallStatus('连接状态:'+pc.connectionState);
    if(pc.connectionState==='connected'){callState='connected';setCallStatus('已接通(E2E 加密通话)');}
    if(['disconnected','failed','closed'].includes(pc.connectionState))endCall(true);
  };
}

// 挂本地轨并把方向显式提升为 sendrecv,本地缺的媒体补 recvonly。
// 关键:addTrack 后必须把该 transceiver.direction 设为 sendrecv——
// 否则若 transceiver 已是 recvonly(如先 setRemoteDescription 建好的),addTrack 不会自动提升,
// 应答方 answer 会被压成 recvonly 只收不发(probe 实测证实)。双向都要调用本函数。
function applyLocalTracks(){
  if(!pc||!localStream)return;
  // 跳过借用的语音触发轨(_voiceTrigger):它只为强制通信音频模式而采集,不发送给对方。
  const sendTracks = localStream.getTracks().filter(t=>!t._voiceTrigger);
  sendTracks.forEach(t=>{
    pc.addTrack(t, localStream);
  });
  const hasAudio = sendTracks.some(t=>t.kind==='audio');
  const hasVideo = sendTracks.some(t=>t.kind==='video');
  // 显式提升已挂轨的 transceiver 方向为 sendrecv
  pc.getTransceivers().forEach(tc=>{
    if(tc.sender.track){  // 有本地轨 → 要能发也要能收
      tc.direction='sendrecv';
    }
  });
  // 本地缺的媒体补 recvonly(只收对方,不发),保证仍能收到对方该媒体
  const kinds = pc.getTransceivers().map(tc=>tc.receiver.track?tc.receiver.track.kind:(tc.sender.track?tc.sender.track.kind:null));
  try{
    if(!hasAudio && !kinds.includes('audio')) pc.addTransceiver('audio',{direction:'recvonly'});
    if(!hasVideo && !kinds.includes('video')) pc.addTransceiver('video',{direction:'recvonly'});
  }catch(e){}
}



function waitIce(){
  return new Promise(res=>{
    if(pc.iceGatheringState==='complete')return res();
    let n=0;
    const iv=setInterval(()=>{
      n++;
      if(pc.iceGatheringState==='complete'||n>20){clearInterval(iv);res();}
    },150);
  });
}

// 信令经 WS 实时推送(ws.onmessage → handleSignal),无需轮询 loop。
async function handleSignal(sig){
  if(!sig||!sig.type)return;
  try{
    if(sig.type==='offer'){
      // 防御:SDP 必须以 v= 开头(过滤历史里的测试/伪造 offer)
      if(!sig.sdp||!String(sig.sdp).startsWith('v='))return;
      // 被叫:只有"真的在通话中"(pc 存在且未关闭/未 failed)才回 busy。
      const inRealCall = pc && !['closed','failed','disconnected'].includes(pc.connectionState) && pc.connectionState!=='new';
      if(callState!=='idle' && inRealCall){await sendSig({type:'busy'});return;}
      if(callState!=='idle'){endCall(true);}
      // 不自动接听 —— 弹出来电提示,由用户选择"接听/拒绝"(对齐主流 IM 习惯)
      showIncomingCall(sig);
      return;
    }else if(sig.type==='answer'){
      // 主叫收 answer:必须有活跃 pc + 合法 SDP 才处理
      if(pc&&callState==='calling'&&sig.sdp&&String(sig.sdp).startsWith('v=')){await pc.setRemoteDescription({type:'answer',sdp:sig.sdp});}
    }else if(sig.type==='reoffer'){
      // 通话中收到对方的重协商 offer(对方加了屏幕轨等)。与来电 offer 完全分离,不弹来电框。
      if(!pc||!sig.sdp||!String(sig.sdp).startsWith('v='))return;
      _dbg('reoffer-recv',{videoM:(sig.sdp.match(/m=video/g)||[]).length, polite:!isCaller, making:_makingOffer});
      // glare:自己也在产 reoffer 且同时收到对方的 → polite(被叫)回退让对方赢;impolite(主叫)无视。
      const polite=!isCaller;
      const collision=_makingOffer;
      if(collision && !polite)return;          // 主叫且自己也在 offer:忽略对方,等其接受我的
      try{
        await pc.setRemoteDescription({type:'offer',sdp:sig.sdp});
        await pc.setLocalDescription(await pc.createAnswer());
        sendSig({type:'reanswer', sdp:pc.localDescription.sdp});
        _dbg('reanswer-sent',{});
      }catch(e){setCallStatus('处理重协商失败:'+e);}
    }else if(sig.type==='reanswer'){
      // 收重协商应答:无状态限制(可能在 connected),只要有活跃 pc + 合法 SDP
      _dbg('reanswer-recv',{hasPc:!!pc});
      if(pc&&sig.sdp&&String(sig.sdp).startsWith('v=')){try{await pc.setRemoteDescription({type:'answer',sdp:sig.sdp});_dbg('reanswer-applied',{});}catch(e){setCallStatus('reanswer失败:'+e);}}
    }else if(sig.type==='screenstop'){
      // 对方停止了屏幕共享 → 撤掉大屏(主路径;onended 是兜底)
      _removeScreenView();
    }else if(sig.type==='ice'){
      if(pc&&sig.candidate){try{await pc.addIceCandidate(sig.candidate);}catch(e){}}
    }else if(sig.type==='end'){
      setCallStatus('对方已挂断');endCall(true);
    }else if(sig.type==='busy'){
      setCallStatus('对方忙线中');endCall(true);
    }else if(sig.type==='reject'){
      setCallStatus('对方拒绝了来电');endCall(true);
    }else if(sig.type==='noanswer'){
      setCallStatus('对方未接听');endCall(true);
    }
  }catch(e){setCallStatus('信令处理异常:'+e);}
}

// ─── 来电提示(接听/拒绝)— 显式模态,不被联系人列表/空会话遮挡 ───────────
let _pendingOffer=null, _ringTimer=null;
function showIncomingCall(sig){
  _pendingOffer=sig;
  // 移除旧的来电模态
  document.querySelectorAll('.incomingCallModal').forEach(e=>e.remove());
  const callerName = sig.from || (cur?cur.display_name:'对方');
  const modal=document.createElement('div');modal.className='incomingCallModal';
  modal.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.75);display:flex;align-items:center;justify-content:center;z-index:2000';
  modal.innerHTML=`<div style="background:var(--panel);padding:28px 32px;border-radius:16px;text-align:center;max-width:340px;border:2px solid var(--ok)">
    <div style="font-size:40px;margin-bottom:8px">📞</div>
    <div style="font-size:18px;font-weight:600;margin-bottom:4px">${esc(callerName)}</div>
    <div style="font-size:14px;color:var(--dim);margin-bottom:18px">${sig.video?'视频通话':'语音通话'} · 等待你接听</div>
    <div style="display:flex;gap:14px;justify-content:center">
      <button id="incAcc" style="background:var(--ok);border:none;color:#fff;padding:11px 26px;border-radius:10px;cursor:pointer;font-size:16px;font-weight:600">接听</button>
      <button id="incRej" style="background:var(--bad);border:none;color:#fff;padding:11px 26px;border-radius:10px;cursor:pointer;font-size:16px;font-weight:600">拒绝</button>
    </div></div>`;
  document.body.appendChild(modal);
  modal.querySelector('#incAcc').onclick=()=>{modal.remove();acceptCall(sig);};
  modal.querySelector('#incRej').onclick=()=>{modal.remove();rejectCall();};
  // 30s 未接自动按"未接"处理(通知主叫)
  _ringTimer=setTimeout(()=>{modal.remove();rejectCall(true);},30000);
  setCallStatus('来电:'+callerName+'('+(sig.video?'视频':'语音')+'),等待你接听');
}

async function acceptCall(sig){
  if(_ringTimer){clearTimeout(_ringTimer);_ringTimer=null;}
  _pendingOffer=null;
  isCaller=false;callState='answering';callStartTs=Date.now();
  showCallPanel(true);setCallStatus('接听中…');
  try{
    const me = window._activeUser||'me';
    const callerName = sig.from || (cur?cur.display_name:'');
    currentRoom = _callRoom(me, callerName);
    wsJoin(currentRoom, myPeerId, true);
    if(!cur && callerName){ cur = {contact_id: 0, display_name: callerName}; }
    // 关键修复 NotReadableError:先尝试 getUserMedia;若视频源不可用,降级为仅音频,不让整个接听失败
    localStream=await getMediaSafe(sig.video);
    document.getElementById('localVideo').srcObject=localStream;
    setupPeer();
    await pc.setRemoteDescription({type:'offer',sdp:sig.sdp});
    applyLocalTracks();   // setRemoteDescription 之后挂本地轨并把方向提升为 sendrecv
    const ans=await pc.createAnswer();
    await pc.setLocalDescription(ans);
    await waitIce();
    sendSig({type:'answer', sdp:pc.localDescription.sdp});
    setCallStatus('已接听,建立连接中…');
  }catch(e){setCallStatus('接听失败:'+e);callState='idle';}
}

function rejectCall(timeout){
  if(_ringTimer){clearTimeout(_ringTimer);_ringTimer=null;}
  _pendingOffer=null;
  sendSig({type: timeout?'noanswer':'reject'});
  setCallStatus(timeout?'未接听':'已拒绝来电');
}

// getUserMedia 安全降级:视频源不可用时退化为仅音频,而不是整个失败(NotReadableError)
async function getMediaSafe(video){
  if(video){
    try{ return await getMedia(true); }
    catch(e){
      setCallStatus('视频源不可用,已切换为语音通话');
      try{ return await _getVoiceStream(); }catch(e2){ throw e2; }
    }
  }
  return await _getVoiceStream();
}

// 纯语音通话:采集 {audio, video} 但把视频轨 disable 且不发送。
// 为什么带视频轨:只请求 audio 时,安卓 Chrome 走普通媒体录音路径,系统级 AEC 不生效
// (这就是"视频通话防啸好、纯语音啸叫严重"的根因)。带一条视频轨会强制浏览器进入
// 通信音频模式(AUDIO_MODE_IN_COMMUNICATION),系统 AEC/降噪才启用。视频轨仅借用采集,
// 不 addTrack 发给对方(见 applyLocalTracks 的 voiceOnly 分支)。
async function _getVoiceStream(){
  try{
    const s = await navigator.mediaDevices.getUserMedia({
      audio: _audioConstraints(),
      video: {width:{ideal:160},frameRate:{ideal:5},facingMode:{ideal:'user'}},  // 极小分辨率,只借通信模式
    });
    // 视频轨标记为"借用的语音触发轨",不发送、不预览
    s.getVideoTracks().forEach(t=>{ t.enabled=false; t._voiceTrigger=true; });
    return s;
  }catch(e){
    // 某些设备拿不到视频(无摄像头),退回纯音频(AEC 弱但能用)
    return await navigator.mediaDevices.getUserMedia({audio: _audioConstraints()});
  }
}

// 抽出音频约束,供 getMedia / _getVoiceStream 复用
function _audioConstraints(){
  return {
    echoCancellation: {ideal: true},
    echoCancellationType: {ideal: 'system'},
    noiseSuppression: {ideal: true},
    autoGainControl: {ideal: true},
    channelCount: {ideal: 1},
    googEchoCancellation: {ideal: true},
    googAutoGainControl: {ideal: true},
    googNoiseSuppression: {ideal: true},
    googHighpassFilter: {ideal: true},
    googTypingNoiseDetection: {ideal: true},
  };
}

function toggleMic(){
  if(!localStream)return;
  const t=localStream.getAudioTracks()[0];if(!t)return;
  t.enabled=!t.enabled;
  document.getElementById('micBtn').textContent=t.enabled?'🎤 静音':'🎤 已静音';
}
function toggleCam(){
  if(!localStream)return;
  const t=localStream.getVideoTracks()[0];if(!t)return;
  t.enabled=!t.enabled;
  document.getElementById('camBtn').textContent=t.enabled?'📷 关视频':'📷 已关';
}
async function shareScreen(){
  if(!pc){alert('先建立通话');return;}
  try{
    if(screenTrack){ // 停止共享
      // 显式通知对方撤掉大屏(无论新增 m-line 还是复用 transceiver,统一靠信令,接收端不猜)
      try{ sendSig({type:'screenstop'}); }catch(e){}
      const sender=pc.getSenders().find(s=>s.track===screenTrack);
      if(sender){
        // 复用场景:replaceTrack(null) 清空而非 removeTrack(避免动 m-line 结构);
        // 新增场景(摄像头开着时的第二条视频轨):removeTrack 移除整条。
        const tc=pc.getTransceivers().find(t=>t.sender===sender);
        if(tc && tc.receiver.track && tc.receiver.track.kind==='video' && !document.getElementById('localVideo').srcObject){
          try{ await sender.replaceTrack(null); tc.direction='recvonly'; }catch(e){ try{pc.removeTrack(sender);}catch(e2){} }
        } else {
          try{ pc.removeTrack(sender); }catch(e){}
        }
      }
      screenTrack.stop();screenTrack=null;
      document.getElementById('screenBtn').textContent='🖥️ 共享屏幕';
      return;
    }
    // getDisplayMedia 在某些环境(老浏览器/非安全上下文/部分安卓 WebView)不可用 —— 明确提示而不是报 TypeError
    if(!navigator.mediaDevices || typeof navigator.mediaDevices.getDisplayMedia!=='function'){
      setCallStatus('此浏览器不支持屏幕共享(需较新 Chrome + 安全上下文)');
      return;
    }
    const disp=await navigator.mediaDevices.getDisplayMedia({video:{frameRate:{ideal:10},displaySurface:'monitor'},audio:false});
    screenTrack=disp.getVideoTracks()[0];
    screenTrack.contentHint='detail';
    // ── 复用空闲 video transceiver,避免新增 m-line 改变顺序 ──────────────
    // 纯语音/摄像头关的通话,video m-line 已被一个 sender.track=null 的 recvonly transceiver 占位。
    // 若直接 addTrack 屏幕轨会新建 m-line、重排顺序 → InvalidAccessError "order of m-lines doesn't match"。
    // 改为:找那个空闲 video transceiver,replaceTrack 塞屏幕轨 + direction 提为 sendrecv,m-line 数与顺序不变。
    const idleVideoTc = pc.getTransceivers().find(tc=>tc.receiver.track && tc.receiver.track.kind==='video' && !tc.sender.track);
    if(idleVideoTc){
      await idleVideoTc.sender.replaceTrack(screenTrack);
      idleVideoTc.direction='sendrecv';
      _dbg('share-reuseTc',{dir:idleVideoTc.direction});
    }else{
      // 摄像头正开着(已有活跃 video sender) → 屏幕是第二条视频轨,只能新建 m-line
      pc.addTrack(screenTrack,disp);
      _dbg('share-addTrack',{senders:pc.getSenders().length, pcState:pc.connectionState});
    }
    screenTrack.onended=()=>{document.getElementById('screenBtn').textContent='🖥️ 共享屏幕';screenTrack=null;};
    document.getElementById('screenBtn').textContent='🖥️ 停止共享';
  }catch(e){setCallStatus('屏幕共享失败:'+e);}
}

async function endCall(remote){
  if(!remote)sendSig({type:'end'});
  if(screenTrack){screenTrack.stop();screenTrack=null;}
  _removeScreenView();  // 挂断时清掉大屏(无论是自己在看还是对方在看)
  if(pc){try{pc.close();}catch(e){}pc=null;}
  if(localStream){localStream.getTracks().forEach(t=>t.stop());localStream=null;}
  document.getElementById('localVideo').srcObject=null;
  document.getElementById('remoteVideo').srcObject=null;
  callState='idle';
  setTimeout(()=>showCallPanel(false),1500);
}

function renderInviteQr(text){
  const cv=document.getElementById('inviteQr');
  const msg=document.getElementById('inviteQrMsg');
  try{
    const qr=qrcode(0,'M');  // 0=自动版本号,M=纠错级
    qr.addData(text);
    qr.make();
    const n=qr.getModuleCount();
    const qz=2;  // quiet zone(格)
    const cell=240/(n+2*qz);
    const ctx=cv.getContext('2d');
    ctx.fillStyle='#ffffff';ctx.fillRect(0,0,240,240);
    ctx.fillStyle='#000000';
    for(let r=0;r<n;r++){
      for(let c=0;c<n;c++){
        if(qr.isDark(r,c)){
          ctx.fillRect(Math.round((c+qz)*cell),Math.round((r+qz)*cell),Math.ceil(cell),Math.ceil(cell));
        }
      }
    }
    cv.style.display='block';msg.style.display='none';
  }catch(e){
    // 兜底:文本过长/编码失败 -> 隐藏 canvas,只留文本链接
    cv.style.display='none';msg.style.display='block';
  }
}
async function copyInvite(){
  const t=document.getElementById('inviteLink');
  const text=t.value;
  let ok=false;
  try{
    if(navigator.clipboard&&navigator.clipboard.writeText){
      await navigator.clipboard.writeText(text);ok=true;
    }
  }catch(e){ok=false;}
  if(!ok){
    // 兜底:已弃用的 execCommand(http 上下文/权限拒绝时)
    try{t.select();ok=document.execCommand('copy');}catch(e){ok=false;}
  }
  const btn=document.querySelector('#inviteModal button[onclick="copyInvite()"]');
  if(btn&&ok){const o=btn.textContent;btn.textContent='已复制';setTimeout(()=>{btn.textContent=o;},1500);}
}
async function trustFlow(){
  if(!cur){alert('先选中一个联系人');return;}
  const r=await api('/dm/api/trust_establish',{method:'POST',body:JSON.stringify({contact:String(cur.contact_id)})});
  alert(r.ok?('已与 '+cur.display_name+' 建立文件签名信任根,可互发签名文件。'):('失败:'+(r.error||r.diagnosable||'')));
}
function closeInvite(){document.getElementById('inviteModal').style.display='none';}
// 来电监听经 WS(方案 A):页面加载即连 WS 并加入"自己身份的等待房间",
// 来电 offer 经服务器实时推送触发 handleSignal,不轮询、无残留状态。
// 注意:startSignal 已在 ws.onmessage 里统一调 handleSignal,这里只需确保 WS 常连 + 加入等待房间。
function startWsListener(){
  ensureWs(()=>{
    // 加入"我的收件房间"(以我的身份命名),主叫方向这里发 offer。收件房间不是通话房间,不写 currentRoom。
    wsJoin(_inboxRoom(window._activeUser||'me'), myPeerId, false);
  });
}
document.getElementById('in').addEventListener('keydown',e=>{if(e.key==='Enter')sendMsg();});
refresh();setInterval(()=>{if(cur)openChat(cur,true);else loadContacts();pollA2H();},5000);
startWsListener();

// ─── 媒体权限预热(Bug 2):把"系统授权弹窗"从"接听瞬间"提前到"来电之前" ───
// 浏览器策略:getUserMedia 必须由用户手势触发,页面加载即调会被静默拒绝并可能污染权限状态。
// 因此挂一个 once 的 pointerdown/keydown:首次交互后静默采一次流,只为了让系统记住授权,
// 立即 release 所有轨,不占用摄像头/麦克风。拒绝则静默放弃,不影响后续 acceptCall 正常再申请。
// 复用 getMediaSafe(false)(内部 _getVoiceStream 会尽量连视频权限一起申请,覆盖语音/视频两种来电)。
(function(){
  let _preheated=false;
  async function _preheatMedia(){
    if(_preheated)return;_preheated=true;
    try{
      const s=await getMediaSafe(false);
      if(s)s.getTracks().forEach(t=>t.stop());
    }catch(e){/* 用户拒绝/无设备:静默,接听时再正常申请 */}
  }
  const once={once:true};
  window.addEventListener('pointerdown',_preheatMedia,once);
  window.addEventListener('keydown',_preheatMedia,once);
})();
</script>
</body></html>
"""


def _render_page():
    """把内联 QR 库作为独立 <script> 块注入到 TOKEN 定义之前。

    纯字符串内联,无任何外部 script/img/fetch,符合 SecureDM 离线约束。
    """
    block = "<script>\n" + _QRCODE_LIB + "\n</script>\n<script>"
    return _PAGE.replace("<script>", block, 1)


# ────────────────────────────────────────────────────────────────────── #
# HTTP 处理
# ────────────────────────────────────────────────────────────────────── #

class DMHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _auth(self) -> bool:
        if not _ACCESS_TOKEN:
            return True
        if self.headers.get("X-L4-Token", "") == _ACCESS_TOKEN:
            return True
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return (q.get("token") or [""])[0] == _ACCESS_TOKEN

    def _json(self, o, code=200):
        b = json.dumps(o, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _html(self, s):
        b = s.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path, q = u.path, urllib.parse.parse_qs(u.query)
        if path in ("/", "/dm"):
            if not self._auth():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            return self._html(_render_page())
        if not self._auth():
            return self._json({"ok": False, "error": "unauthorized"}, 401)
        if path == "/dm/api/status":
            return self._json(api_status())
        if path == "/dm/api/contacts":
            return self._json(api_contacts())
        if path == "/dm/api/history":
            return self._json(api_history((q.get("contact") or [""])[0], int((q.get("limit") or [60])[0])))
        if path == "/dm/api/verify_file":
            return self._json(api_verify_file((q.get("contact") or [""])[0], (q.get("file_name") or [""])[0]))
        if path == "/dm/api/a2h_status":
            return self._json(api_a2h_status())
        if path == "/dm/api/file_info":
            return self._json(api_file_info((q.get("path") or [""])[0]))
        if path == "/dm/api/call_poll":
            try:
                sid = int((q.get("since_id") or ["0"])[0])
            except ValueError:
                sid = 0
            return self._json(api_call_poll((q.get("contact") or [""])[0], sid))
        return self._json({"ok": False, "error": "unknown path"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if not self._auth():
            return self._json({"ok": False, "error": "unauthorized"}, 401)
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode()) if length else {}
        except Exception:  # noqa: BLE001
            return self._json({"ok": False, "error": "invalid json"}, 400)
        if path == "/dm/api/setup":
            # 缺省传 ""(不是 "oiagent"):让 api_setup 的 "display_name or DM_IDENTITY" 生效,
            # 否则硬编码缺省值永远覆盖 DM_IDENTITY,bob 实例被错建成 oiagent。
            return self._json(api_setup(req.get("display_name", "")))
        if path == "/dm/api/create_invite":
            return self._json(api_create_invite())
        if path == "/dm/api/create_invite_incognito":
            return self._json(api_create_invite_incognito())
        if path == "/dm/api/get_identity":
            return self._json(api_get_identity())
        if path == "/dm/api/set_identity":
            return self._json(api_set_identity(str(req.get("name", ""))))
        if path == "/dm/api/db_password_status":
            return self._json(api_db_password_status())
        if path == "/dm/api/db_set_password":
            return self._json(api_db_set_password(str(req.get("password", ""))))
        if path == "/dm/api/db_unlock":
            return self._json(api_db_unlock(str(req.get("password", ""))))
        if path == "/dm/api/db_change_password":
            return self._json(api_db_change_password(str(req.get("old_password", "")), str(req.get("new_password", ""))))
        if path == "/dm/api/new_user_id":
            return self._json(api_new_user_id())
        if path == "/dm/api/create_address":
            return self._json(api_create_address())
        if path == "/dm/api/accept_invite":
            return self._json(api_accept_invite(req.get("link", "")))
        if path == "/dm/api/delete_contact":
            return self._json(api_delete_contact(str(req.get("contact", ""))))
        if path == "/dm/api/send":
            return self._json(api_send(str(req.get("contact", "")), req.get("text", ""), int(req.get("ttl", 0) or 0)))
        if path == "/dm/api/receive_file":
            return self._json(api_receive_file(int(req.get("file_id", 0))))
        if path == "/dm/api/get_download_dir":
            return self._json(api_get_download_dir())
        if path == "/dm/api/set_download_dir":
            return self._json(api_set_download_dir(str(req.get("path", ""))))
        if path == "/dm/api/send_file_signed":
            return self._json(api_send_file_signed(str(req.get("contact", "")), req.get("path", "")))
        if path == "/dm/api/send_file":
            return self._json(api_send_file(str(req.get("contact", "")), req.get("path", ""), bool(req.get("signed", False))))
        if path == "/dm/api/file_info":
            return self._json(api_file_info(req.get("path", "")))
        if path == "/dm/api/a2h_status":
            return self._json(api_a2h_status())
        if path == "/dm/api/a2h_set_approver":
            return self._json(api_a2h_set_approver(str(req.get("contact", ""))))
        if path == "/dm/api/trust_establish":
            return self._json(api_trust_establish(str(req.get("contact", ""))))
        if path == "/dm/api/trust_import":
            return self._json(api_trust_import(str(req.get("contact", "")), req.get("key", "")))
        if path == "/dm/api/call_signal":
            return self._json(api_call_signal(str(req.get("contact", "")), req.get("signal", {})))
        return self._json({"ok": False, "error": "unknown path"}, 404)


def run(host: str = DM_HOST, port: int = DM_PORT) -> None:
    srv = ThreadingHTTPServer((host, port), DMHandler)
    if _ACCESS_TOKEN:
        print(f"SecureDM 就绪: http://{host}:{port}/?token={_ACCESS_TOKEN}")
    else:
        print(f"SecureDM 就绪: http://{host}:{port}  (token 禁用)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    # argv 优先于 env(Windows Start-Process 传 env 常失败,argv 100% 可靠):
    #   python securedm_web.py <port> [identity] [db_prefix]
    # 也支持 PORT env 变量(供 preview 工具 autoPort 用)
    _port_env = os.environ.get("PORT")
    p = int(sys.argv[1] if len(sys.argv) > 1 else (_port_env if _port_env else DM_PORT))
    if len(sys.argv) > 2 and sys.argv[2]:
        DM_IDENTITY = sys.argv[2]
    if len(sys.argv) > 3 and sys.argv[3]:
        DM_DB_PREFIX = sys.argv[3]
    run(port=p)
