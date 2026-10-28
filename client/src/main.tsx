import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import { AuthenticationBoundary } from "./root"

import { captureSetupLink } from "./lib/setup"

captureSetupLink()

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthenticationBoundary />
  </StrictMode>,
)
