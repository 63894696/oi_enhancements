package ai.prisir.remote;

import android.os.Bundle;
import android.webkit.CookieManager;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
  @Override
  public void onCreate(Bundle savedInstanceState) {
    super.onCreate(savedInstanceState);
    // 遥控器用 iframe 加载 PC 对话 UI(http://<pc>:18802/?token=)。
    // PC 端配对通过后会 Set-Cookie 授权,iframe 内后续 fetch/img 依赖该 cookie。
    // Android WebView 默认丢弃第三方(iframe)cookie → 必须显式开启,
    // 否则会话/模型配置/图标全被 401(用户实测「和 Win 端不一样」的根因)。
    CookieManager cm = CookieManager.getInstance();
    cm.setAcceptCookie(true);
    if (getBridge() != null && getBridge().getWebView() != null) {
      cm.setAcceptThirdPartyCookies(getBridge().getWebView(), true);
    }
  }

  @Override
  public void onStart() {
    super.onStart();
    // Bridge 在 onStart 后才就绪,补一次确保 thirdPartyCookies 真开上。
    try {
      CookieManager cm = CookieManager.getInstance();
      cm.setAcceptCookie(true);
      if (getBridge() != null && getBridge().getWebView() != null) {
        cm.setAcceptThirdPartyCookies(getBridge().getWebView(), true);
      }
    } catch (Exception ignored) {}
  }
}
