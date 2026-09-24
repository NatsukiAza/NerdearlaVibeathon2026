import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ProgramPage } from "./audience/ProgramPage";
import { SessionPage } from "./audience/SessionPage";
import { OverlayPage } from "./overlay/OverlayPage";
import { ProductionPage } from "./production/ProductionPage";

/**
 * Three surfaces, three layouts — overlay and audience do not share chrome.
 */
export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<ProgramPage />} />
        <Route path="/sessions/:id" element={<SessionPage />} />
        <Route path="/sessions/:id/overlay" element={<OverlayPage />} />
        <Route path="/production" element={<ProductionPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
