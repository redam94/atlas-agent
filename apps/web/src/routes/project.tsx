import { useParams } from "react-router-dom";

export function ProjectRoute() {
  const { id } = useParams<{ id: string }>();
  return <div className="p-6">Project: {id}</div>;
}
