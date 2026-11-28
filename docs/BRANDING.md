# SuperNova 品牌图标规范

## 统一 Logo 资源

- 前端统一使用 `apps/console/public/logo-rounded.svg`。
- SVG 内嵌原始 256×256 Logo 图像，并通过圆角矩形裁切；保留原始图案、颜色和比例。
- 浏览器标签页 favicon、登录/注册页、后台侧边栏、工作台首页均使用同一资源。
- 前端展示的 Logo 图片统一使用 `border-radius: 20%`，形成圆角方形，不使用圆形裁切或直角方形。
- 原始 `logo.ico` 保留为源素材，不作为当前 UI 的显示入口。

## 变更原则

新增品牌展示位置时，优先复用 `/logo-rounded.svg`，不要重新上传或绘制另一份 Logo。
