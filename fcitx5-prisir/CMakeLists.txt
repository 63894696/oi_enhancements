cmake_minimum_required(VERSION 3.16)
project(fcitx5-prisir VERSION 1.0.0)

find_package(Fcitx5Core REQUIRED)
find_package(Fcitx5Utils REQUIRED)

include("${FCITX_INSTALL_CMAKECONFIG_DIR}/Fcitx5Utils/Fcitx5CompilerSettings.cmake")

add_library(prisir MODULE src/prisir.cpp)
target_link_libraries(prisir Fcitx5::Core ${CMAKE_DL_LIBS})
set_target_properties(prisir PROPERTIES PREFIX "")

# addon .so → fcitx5 模块目录
install(TARGETS prisir DESTINATION "${FCITX_INSTALL_ADDONDIR}")
# addon 描述 → fcitx5 addon 目录
install(FILES prisir.conf.in RENAME prisir.conf DESTINATION "${FCITX_INSTALL_PKGDATADIR}/addon")
# 输入法声明 → fcitx5 inputmethod 目录
install(FILES prisir-inputmethod.conf RENAME prisir.conf DESTINATION "${FCITX_INSTALL_PKGDATADIR}/inputmethod")
