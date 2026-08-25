import { useMemo, useState } from "react";
import Onboarding from "./Onboarding";
import Welcome from "./Welcome";
import ProjectChooser from "./ProjectChooser";
import { createFirebaseAuthGateway, type AuthGateway, type AuthSession } from "./auth";
import { createControlApiClient, ControlApiError, waitForWorkflow, type ControlApiClient, type Project, type ResultView, type WorkflowProgress } from "./apiClient";
import type { OnboardingValues } from "./onboardingState";
import { internalLabel, uniqueLabels, userError } from "./presentation";
import { ResultScreen } from "./result/ResultScreen";
import { WorkflowProgressView } from "./WorkflowProgressView";

type AppScreen = "welcome" | "projects" | "onboarding" | "analysis" | "result";

export interface AppProps {
  authGateway?: AuthGateway;
  apiFactory?: (session: AuthSession) => ControlApiClient;
}

export default function App({ authGateway, apiFactory }: AppProps = {}) {
  const auth = useMemo(
    () => authGateway ?? createFirebaseAuthGateway(),
    [authGateway],
  );
  const [screen, setScreen] = useState<AppScreen>("welcome");
  const [client, setClient] = useState<ControlApiClient | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [result, setResult] = useState<ResultView | null>(null);
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [progress, setProgress] = useState<WorkflowProgress | null>(null);
  const [projectBusyId, setProjectBusyId] = useState<string | null>(null);
  const [projectError, setProjectError] = useState("");

  const connectSession = async (session: AuthSession) => {
    const nextClient = apiFactory
      ? apiFactory(session)
      : createControlApiClient(session);
    const savedProjects = await nextClient.listProjects();
    setClient(nextClient);
    if (savedProjects.length) {
      setProjects(savedProjects);
      setScreen("projects");
      window.scrollTo({ top: 0 });
      return;
    }
    const nextProject = await nextClient.createProject();
    setProject(nextProject);
    setScreen("onboarding");
    window.scrollTo({ top: 0 });
  };

  const start = async () => {
    setLoginBusy(true);
    setLoginError("");
    try {
      const session = await auth.signIn();
      await connectSession(session);
    } catch (error) {
      setLoginError(userError(error, "Google 로그인에 실패했습니다."));
    } finally {
      setLoginBusy(false);
    }
  };

  const createProject = async () => {
    if (!client) return;
    setLoginBusy(true);
    setProjectError("");
    try {
      const nextProject = await client.createProject();
      setProject(nextProject);
      setProjects((current) => [...current, nextProject]);
      setScreen("onboarding");
      window.scrollTo({ top: 0 });
    } catch (error) {
      setProjectError(userError(error, "새 창업 검토를 만들지 못했습니다."));
    } finally {
      setLoginBusy(false);
    }
  };

  const resumeProject = async (nextProject: Project) => {
    if (!client) return;
    setProjectBusyId(nextProject.project_id);
    setProjectError("");
    setProject(nextProject);
    setProgress(null);
    try {
      if (!nextProject.state) {
        setScreen("onboarding");
        window.scrollTo({ top: 0 });
        return;
      }
      try {
        const savedResult = await client.getResult(nextProject.project_id);
        setResult(savedResult);
        setScreen("result");
        window.scrollTo({ top: 0 });
        return;
      } catch (error) {
        if (!(error instanceof ControlApiError) || error.status !== 404)
          throw error;
        setScreen("analysis");
        window.scrollTo({ top: 0 });
        const workflow = await client.startFirstProposal(
          nextProject.project_id,
        );
        const terminal = await waitForWorkflow(
          client,
          nextProject.project_id,
          workflow,
          setProgress,
        );
        if (!["SUCCEEDED", "PARTIAL"].includes(terminal.status))
          throw new Error("저장된 분석을 이어서 완료하지 못했습니다.", {
            cause: error,
          });
        const savedResult = await client.getResult(nextProject.project_id);
        setResult(savedResult);
        setScreen("result");
        window.scrollTo({ top: 0 });
      }
    } catch (error) {
      setProjectError(
        userError(error, "저장된 창업 검토를 불러오지 못했습니다."),
      );
    } finally {
      setProjectBusyId(null);
    }
  };

  const completeOnboarding = async (
    values: OnboardingValues,
    areaSelectionToken: string,
  ) => {
    if (!client || !project)
      throw new Error("프로젝트 연결이 준비되지 않았습니다.");
    const confirmedProject = project.state
      ? project
      : await client.confirmOnboarding(
          project.project_id,
          values,
          areaSelectionToken,
        );
    setProject(confirmedProject);
    const workflow = await client.startFirstProposal(
      confirmedProject.project_id,
    );
    const terminal = await waitForWorkflow(
      client,
      confirmedProject.project_id,
      workflow,
      setProgress,
    );
    if (terminal.status === "WAITING_FOR_HUMAN") return;
    if (!["SUCCEEDED", "PARTIAL"].includes(terminal.status)) {
      const reasons = uniqueLabels(terminal.terminal_reason_codes).join(" · ");
      throw new Error(
        `첫 분석이 완료되지 않았습니다: ${internalLabel(terminal.status)}${reasons ? ` (${reasons})` : ""}`,
      );
    }
    const nextResult = await client.getResult(confirmedProject.project_id);
    setResult(nextResult);
    setScreen("result");
    window.scrollTo({ top: 0 });
  };

  if (screen === "welcome")
    return <Welcome onStart={start} busy={loginBusy} error={loginError} />;
  if (screen === "projects")
    return (
      <ProjectChooser
        projects={projects}
        busyProjectId={projectBusyId}
        creating={loginBusy}
        error={projectError}
        onResume={(nextProject) => void resumeProject(nextProject)}
        onCreate={() => void createProject()}
      />
    );
  if (screen === "analysis")
    return (
      <main className="analysis-stage" aria-live="polite">
        <div className="analysis-stage__pulse" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <p className="stage-label">저장된 분석</p>
        <h1>
          {projectError
            ? "분석을 이어서 완료하지 못했어요"
            : "저장된 조건으로 분석을 이어가고 있어요"}
        </h1>
        <p>
          {projectError ||
            "프로젝트 목록을 벗어나도 분석은 계속됩니다. 현재 단계를 확인해 주세요."}
        </p>
        {!projectError && progress && <WorkflowProgressView progress={progress} />}
        {projectError && (
          <button
            className="btn btn--accent"
            type="button"
            onClick={() => {
              setProjectError("");
              setProgress(null);
              setScreen("projects");
              window.scrollTo({ top: 0 });
            }}
          >
            프로젝트 목록으로
          </button>
        )}
      </main>
    );
  if (screen === "onboarding")
    return (
      <>
        <Onboarding
          onComplete={completeOnboarding}
          searchAreas={async (query) => {
            if (!client || !project)
              throw new Error("프로젝트 연결이 준비되지 않았습니다.");
            return (await client.searchAreas(project.project_id, query))
              .candidates;
          }}
        />
        {progress && <div className="workflow-progress"><WorkflowProgressView progress={progress} compact /></div>}
      </>
    );
  if (client && project && result)
    return (
      <ResultScreen client={client} project={project} initialResult={result} />
    );
  return (
    <main className="analysis-stage">
      <h1>결과를 불러오지 못했습니다</h1>
      <p>프로젝트 상태를 다시 확인해 주세요.</p>
    </main>
  );
}
