import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "@/components/Layout";
import DealList from "@/pages/DealList";
import DealDetail from "@/pages/DealDetail";
import Upload from "@/pages/Upload";
import Criteria from "@/pages/Criteria";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<DealList />} />
          <Route path="/deals/:id" element={<DealDetail />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/criteria" element={<Criteria />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
