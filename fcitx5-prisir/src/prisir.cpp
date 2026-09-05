// prisir.cpp — 灵犀拼音 fcitx5 输入法引擎 addon。
// 状态模型:per-IC 自管理拼音缓冲(各 InputContext 独立,借鉴 fcitx5-table)。
// 引擎为进程级单例 dlopen(ciku.db 只载一次,各 IC 共享查询)。
#include <fcitx/inputmethodengine.h>
#include <fcitx/inputcontext.h>
#include <fcitx/inputcontextproperty.h>
#include <fcitx/inputpanel.h>
#include <fcitx/candidatelist.h>
#include <fcitx/text.h>
#include <fcitx/addonfactory.h>
#include <fcitx/addonmanager.h>
#include <fcitx/inputmethodentry.h>
#include <fcitx/instance.h>
#include <memory>
#include <unordered_map>
#include <string>
#include <vector>
#include <cstring>
#include "prisir_engine.h"

namespace {

// 极简 JSON 提取:解析 [{"word":"..","weight":N},..] 取 word 字段。
// 引擎输出格式固定(ffi.rs serde_json),无嵌套/转义除 \" \\。
std::vector<std::string> extractWords(const std::string &json) {
    std::vector<std::string> out;
    const std::string key = "\"word\":\"";
    size_t pos = 0;
    while ((pos = json.find(key, pos)) != std::string::npos) {
        size_t start = pos + key.size();
        std::string w;
        for (size_t i = start; i < json.size(); ++i) {
            char c = json[i];
            if (c == '\\' && i + 1 < json.size()) { w += json[i + 1]; ++i; continue; }
            if (c == '"') { pos = i + 1; break; }
            w += c;
        }
        if (!w.empty()) out.push_back(w);
    }
    return out;
}

class PrisirState : public fcitx::InputContextProperty {
public:
    std::string buffer;  // 当前拼音输入缓冲
};

class PrisirEngineAddon;

class PrisirCandidateWord : public fcitx::CandidateWord {
public:
    PrisirCandidateWord(PrisirEngineAddon *engine, std::string word, std::string input)
        : engine_(engine), word_(std::move(word)), input_(std::move(input)) {
        setText(fcitx::Text(word_));
    }
    void select(fcitx::InputContext *ic) const override;

private:
    PrisirEngineAddon *engine_;
    std::string word_;
    std::string input_;
};

class PrisirEngineAddon : public fcitx::InputMethodEngine {
public:
    PrisirEngineAddon(fcitx::Instance *instance) : instance_(instance) {
        // 引擎 .so 与词库路径:addon 安装目录(见 CMake/postinst 布局)。
        const char *base = std::getenv("PRISIR_IME_HOME");
        std::string home = base ? base : "/usr/lib/fcitx5/prisir";
        engine_.open(home + "/libprisir_ime.so", home + "/ciku.db");
    }

    void activate(const fcitx::InputMethodEntry &, fcitx::InputContextEvent &) override {}

    void deactivate(const fcitx::InputMethodEntry &entry, fcitx::InputContextEvent &event) override {
        reset(entry, event);
    }

    void reset(const fcitx::InputMethodEntry &, fcitx::InputContextEvent &event) override {
        auto *ic = event.inputContext();
        state(ic)->buffer.clear();
        ic->inputPanel().reset();
        ic->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel);
        ic->updatePreedit();
    }

    void keyEvent(const fcitx::InputMethodEntry &entry, fcitx::KeyEvent &keyEvent) override {
        FCITX_UNUSED(entry);
        auto *ic = keyEvent.inputContext();
        if (keyEvent.isRelease()) return;
        auto key = keyEvent.key();
        auto *st = state(ic);

        // a-z:累积拼音
        if (key.isSimple() && key.sym() >= FcitxKey_a && key.sym() <= FcitxKey_z) {
            st->buffer += fcitx::Key::keySymToUTF8(key.sym());
            update(ic, st);
            keyEvent.filterAndAccept();
            return;
        }
        // Backspace:删一个字母;空缓冲不拦截
        if (key.check(FcitxKey_BackSpace)) {
            if (!st->buffer.empty()) {
                st->buffer.pop_back();
                update(ic, st);
                keyEvent.filterAndAccept();
            }
            return;
        }
        // 数字 1-9:选候选
        if (key.isSimple() && key.sym() >= FcitxKey_1 && key.sym() <= FcitxKey_9) {
            if (selectIndex(ic, st, key.sym() - FcitxKey_1)) keyEvent.filterAndAccept();
            return;
        }
        // 空格/回车:首选上屏(空格)或原样上屏(回车)
        if (key.check(FcitxKey_space)) {
            if (selectIndex(ic, st, 0)) keyEvent.filterAndAccept();
            return;
        }
        if (key.check(FcitxKey_Return)) {
            if (!st->buffer.empty()) {
                ic->commitString(st->buffer);
                resetBuffer(ic, st);
                keyEvent.filterAndAccept();
            }
            return;
        }
        // 空格翻页(=/- 或 .),Esc 取消
        if (key.check(FcitxKey_equal) || key.check(FcitxKey_period)) { page(ic, 1); keyEvent.filterAndAccept(); return; }
        if (key.check(FcitxKey_minus) || key.check(FcitxKey_comma)) { page(ic, -1); keyEvent.filterAndAccept(); return; }
        if (key.check(FcitxKey_Escape)) {
            if (!st->buffer.empty()) { resetBuffer(ic, st); keyEvent.filterAndAccept(); }
            return;
        }
        // 其余(标点/大写等):若有缓冲先把首选上屏,再放行该键
        if (!st->buffer.empty()) {
            selectIndex(ic, st, 0);
            // 不 filter,让该键继续传给应用
        }
    }

    // 供 CandidateWord::select 调用
    void commitCandidate(fcitx::InputContext *ic, const std::string &word, const std::string &input) {
        ic->commitString(word);
        engine_.learn(input, word);
        auto *st = state(ic);
        resetBuffer(ic, st);
    }

private:
    PrisirState *state(fcitx::InputContext *ic) {
        return ic->propertyFor(&factory_);
    }

    void update(fcitx::InputContext *ic, PrisirState *st) {
        if (st->buffer.empty()) { resetBuffer(ic, st); return; }
        auto candList = std::make_unique<fcitx::CommonCandidateList>();
        candList->setPageSize(5);  // 与 selectIndex 的 pageSize 常量一致,显式不依赖默认
        std::vector<std::string> words;
        if (engine_.ok()) words = extractWords(engine_.queryJson(st->buffer));
        int idx = 0;
        for (auto &w : words) {
            candList->append(std::make_unique<PrisirCandidateWord>(this, w, st->buffer));
            if (++idx >= 50) break;
        }
        // 预编辑显示原始拼音
        fcitx::Text preedit(st->buffer);
        ic->inputPanel().setPreedit(preedit);
        ic->inputPanel().setCandidateList(std::move(candList));
        ic->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel);
        ic->updatePreedit();
    }

    bool selectIndex(fcitx::InputContext *ic, PrisirState *st, int idx) {
        auto cl = ic->inputPanel().candidateList();
        if (!cl || cl->size() == 0) {
            if (!st->buffer.empty()) { ic->commitString(st->buffer); resetBuffer(ic, st); }
            return true;
        }
        // 每页候选数:我们 update() 里建的是 CommonCandidateList,默认页大小 5。
        // 基类 CandidateList 无 pageSize(),用固定页大小 + toPageable()->currentPage() 算全局索引。
        const int pageSize = 5;
        int page = 0;
        if (auto *pageable = cl->toPageable()) page = pageable->currentPage();
        if (page < 0) page = 0;
        int global = page * pageSize + idx;
        if (global < 0 || global >= cl->size()) return false;
        const auto &cw = cl->candidate(global);
        commitCandidate(ic, cw.text().toString(), st->buffer);
        return true;
    }

    void page(fcitx::InputContext *ic, int delta) {
        auto cl = ic->inputPanel().candidateList();
        if (!cl) return;
        auto *pageable = cl->toPageable();
        if (!pageable) return;
        if (delta > 0 && pageable->hasNext()) pageable->next();
        else if (delta < 0 && pageable->hasPrev()) pageable->prev();
        ic->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel);
    }

    void resetBuffer(fcitx::InputContext *ic, PrisirState *st) {
        st->buffer.clear();
        ic->inputPanel().reset();
        ic->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel);
        ic->updatePreedit();
    }

    fcitx::Instance *instance_;
    PrisirEngine engine_;
    fcitx::SimpleInputContextPropertyFactory<PrisirState> factory_;
};

void PrisirCandidateWord::select(fcitx::InputContext *ic) const {
    engine_->commitCandidate(ic, word_, input_);
}

class PrisirFactory : public fcitx::AddonFactory {
public:
    fcitx::AddonInstance *create(fcitx::AddonManager *manager) override {
        FCITX_UNUSED(manager);
        return new PrisirEngineAddon(manager->instance());
    }
};

} // namespace

FCITX_ADDON_FACTORY(PrisirFactory)
