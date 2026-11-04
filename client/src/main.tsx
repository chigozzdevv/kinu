import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import { captureSetupLink } from "./lib/setup"
import { AuthenticationBoundary } from "./root"

const path = window.location.pathname.replace(/\/$/, "")
if (path === "/new" || path === "/new/demo") {
  const hash = path === "/new/demo" ? "#rotation-example" : window.location.hash
  window.history.replaceState(window.history.state, "", `/${window.location.search}${hash}`)
}

captureSetupLink()

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthenticationBoundary />
  </StrictMode>,
)
