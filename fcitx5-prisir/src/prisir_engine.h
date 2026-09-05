// prisir_engine.h — 引擎 cdylib(libprisir_ime.so)的 dlopen 封装。
// 与 Windows ffi.rs 同一套 C ABI:load/query/learn/free_string/free。
// fcitx5 addon 不直接 link 引擎(引擎独立 .so,运行期 dlopen),
// 便于引擎单独升级而不重编 addon。
#pragma once
#include <dlfcn.h>
#include <string>
#include <vector>

struct PrisirCandidate {
    std::string word;
    long weight;
};

class PrisirEngine {
public:
    PrisirEngine() = default;
    ~PrisirEngine() { close(); }

    bool open(const std::string &so_path, const std::string &db_path) {
        close();
        handle_lib_ = dlopen(so_path.c_str(), RTLD_NOW | RTLD_LOCAL);
        if (!handle_lib_) return false;
        p_load = (LoadFn)dlsym(handle_lib_, "prisir_ime_load");
        p_query = (QueryFn)dlsym(handle_lib_, "prisir_ime_query");
        p_learn = (LearnFn)dlsym(handle_lib_, "prisir_ime_learn");
        p_free_str = (FreeStrFn)dlsym(handle_lib_, "prisir_ime_free_string");
        p_free = (FreeFn)dlsym(handle_lib_, "prisir_ime_free");
        if (!p_load || !p_query || !p_learn || !p_free_str || !p_free) {
            close();
            return false;
        }
        engine_ = p_load(db_path.c_str(), 0);
        if (!engine_) { close(); return false; }
        return true;
    }

    void close() {
        if (engine_ && p_free) { p_free(engine_); engine_ = nullptr; }
        if (handle_lib_) { dlclose(handle_lib_); handle_lib_ = nullptr; }
        p_load = nullptr; p_query = nullptr; p_learn = nullptr;
        p_free_str = nullptr; p_free = nullptr;
    }

    bool ok() const { return engine_ != nullptr; }

    // 查询,返回 JSON 数组字符串(调用方负责解析)。失败返回空串。
    std::string queryJson(const std::string &input) {
        if (!engine_) return {};
        char *r = p_query(engine_, input.c_str());
        if (!r) return {};
        std::string out(r);
        p_free_str(r);
        return out;
    }

    void learn(const std::string &input, const std::string &selected) {
        if (engine_) p_learn(engine_, input.c_str(), selected.c_str());
    }

private:
    using LoadFn = void *(*)(const char *, int);
    using QueryFn = char *(*)(void *, const char *);
    using LearnFn = void (*)(void *, const char *, const char *);
    using FreeStrFn = void (*)(char *);
    using FreeFn = void (*)(void *);

    void *handle_lib_ = nullptr;
    void *engine_ = nullptr;
    LoadFn p_load = nullptr;
    QueryFn p_query = nullptr;
    LearnFn p_learn = nullptr;
    FreeStrFn p_free_str = nullptr;
    FreeFn p_free = nullptr;
};
