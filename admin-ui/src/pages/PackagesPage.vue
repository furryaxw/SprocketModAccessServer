<script lang="ts" setup>
import {computed, onBeforeUnmount, onMounted, ref, watch} from "vue";
import {transport, type TransportResponse} from "../api/transport";
import {RequestFailure, responseFailure, type RequestPhase} from "../composables/requestState";
import {useAuthorization} from "../composables/useAuthorization";
import {useRequest} from "../composables/useRequest";
import {useWorkspaceContext} from "../composables/useWorkspaceContext";
import UiDataTable from "../components/UiDataTable.vue";
import RequestState from "../components/RequestState.vue";
import {formatTimestamp} from "../composables/time";
import {useLocale} from "../i18n";
import UiButton from "../components/UiButton.vue";
import UiDialog from "../components/UiDialog.vue";
import UiInput from "../components/UiInput.vue";
import {useToast} from "../composables/useToast";
import PageHeader from "../components/PageHeader.vue";
import PackageMetadataJson from "../components/PackageMetadataJson.vue";
import {newExampleMetadata} from "../composables/packageMetadata";

// 一个版本：归档随版本走，元数据属于包。
// `actions` 只为表格提供一列"操作"的键，不承载数据。
type PackageVersionRow = {
    version: string;
    status?: string;
    archive_size?: number;
    archive_digest?: string;
    created_at?: number;
    actions?: undefined;
};

type PackageRow = {
    package_id: string;
    team_id?: string;
    name: string;
    status?: string;
    metadata: Record<string, unknown>;
    created_at?: number;
    updated_at?: number;
    version_count: number;
    versions: PackageVersionRow[];
    actions?: undefined;
};

type PackagePage = {
    items: PackageRow[];
    total: number;
    limit: number;
    offset: number;
};

// 载荷形态：zip 归档，或单个 DLL（服务端按载荷头 `PK` 判断，前端用同一判据）。
type PayloadKind = "archive" | "single";

type UploadResumeDraft = {
    packageId: string;
    version: string;
    size: number;
    contentType: string;
    fileName: string;
    lastModified: number;
    createKey: string;
    confirmKey: string;
    uploadId?: string;
    uploadUrl?: string;
    sha256?: string;
    uploadedAt?: number;
    expiresAt?: number;
};

const messages: {zh: Record<string, string>; en: Record<string, string>} = {
    zh: {
        title: "Packages",
        description: "管理当前 Team 的 Package 与版本。",
        catalogDescription: "查看跨 Team 的 Package 目录；管理动作按包所属 Team 的权限判定。",
        uploadScopeNote: "上传在 Team 工作区进行；这里是系统 Package 目录。",
        refresh: "刷新",
        package: "Package",
        packageId: "Package ID",
        team: "Team",
        name: "名称",
        version: "版本",
        versionCount: "版本数",
        visibility: "可见性",
        updatedAt: "更新时间",
        createdAt: "创建时间",
        status: "状态",
        size: "大小",
        digest: "摘要",
        versionTime: "时间",
        actions: "操作",
        publishedState: "已发布",
        disabled: "已禁用",
        unpublished: "未发布",
        noWorkspace: "当前没有可用工作区。",
        permission: "没有查看 Package 的权限。",
        invalid: "Package 响应格式无效。",
        bytes: "字节",
        newPackage: "新建 Package",
        createTitle: "新建 Package",
        createMod: "Mod 名",
        createModHint: "例如 my-mod",
        createSubmit: "创建",
        createDone: "Package 已创建。",
        createModRequired: "请填写 Mod 名。",
        upload: "上传 Version",
        uploadTitle: "上传 Version",
        uploadPackage: "目标 Package",
        uploadVersion: "版本号",
        versionHint: "例如 1.0.0",
        selectFile: "选择文件（归档或 .dll）",
        singleNotDll: "单个文件必须是 .dll；多个文件请打包成 zip 归档。",
        startUpload: "开始上传",
        close: "关闭",
        uploadPreparing: "正在创建上传草稿...",
        uploadSending: "正在上传归档...",
        uploadConfirming: "正在确认发布...",
        uploadDone: "Version 已发布。",
        noFile: "请选择归档文件。",
        versionRequired: "请填写版本号。",
        versionExists: "该 Package 版本已存在，版本不可变。请选择其他版本后再试。",
        download: "下载",
        downloadDone: "下载已开始。",
        details: "详情",
        detailsTitle: "Package 详情",
        metadataLabel: "Metadata",
        versionHistory: "版本历史",
        versionHistoryEmpty: "该 Package 还没有版本。",
        manage: "管理",
        manageTitle: "编辑 Package",
        statusChange: "状态",
        save: "保存",
        saved: "Package 已更新。",
        versionStatusSaved: "版本状态已更新。",
        deleteVersion: "删除版本",
        deleteVersionTitle: "确认删除版本",
        deleteVersionBody: "删除后该版本的归档立即不可下载，版本号可以重新上传。",
        versionDeleted: "版本已删除。",
        deletePackage: "删除 Package",
        deletePackageTitle: "确认删除 Package",
        deletePackageBody: "删除后该 Package 与它的全部版本一起消失，版本号可以重新上传。",
        packageDeleted: "Package 已删除。",
        deleteConfirm: "删除",
        resumeDraft: "发现未完成上传草稿。",
        resumeNeedsFile: "重新选择同一个归档后继续上传。",
        resumeConfirm: "归档已上传，可以继续确认发布。",
        resumeMismatch: "请选择草稿对应的归档文件。",
        discardDraft: "放弃草稿",
    },
    en: {
        title: "Packages",
        description: "Manage the Packages and versions of the current Team.",
        catalogDescription: "Browse the cross-Team Package catalog; manage actions are judged by the owning Team's permissions.",
        uploadScopeNote: "Uploads happen in a Team workspace; this is the system Package catalog.",
        refresh: "Refresh",
        package: "Package",
        packageId: "Package ID",
        team: "Team",
        name: "Name",
        version: "Version",
        versionCount: "Versions",
        visibility: "Visibility",
        updatedAt: "Updated",
        createdAt: "Created",
        status: "Status",
        size: "Size",
        digest: "Digest",
        versionTime: "Time",
        actions: "Actions",
        publishedState: "Published",
        disabled: "Disabled",
        unpublished: "Unpublished",
        noWorkspace: "No workspace is available.",
        permission: "You do not have permission to view Packages.",
        invalid: "The Package response is invalid.",
        bytes: "bytes",
        newPackage: "New Package",
        createTitle: "New Package",
        createMod: "Mod name",
        createModHint: "e.g. my-mod",
        createSubmit: "Create",
        createDone: "Package created.",
        createModRequired: "Enter a mod name.",
        upload: "Upload Version",
        uploadTitle: "Upload Version",
        uploadPackage: "Package",
        uploadVersion: "Version",
        versionHint: "e.g. 1.0.0",
        selectFile: "Select a file (archive or .dll)",
        singleNotDll: "A single-file upload must be a .dll; package multiple files as a zip archive.",
        startUpload: "Start upload",
        close: "Close",
        uploadPreparing: "Creating upload draft...",
        uploadSending: "Uploading archive...",
        uploadConfirming: "Confirming publication...",
        uploadDone: "Version published.",
        noFile: "Select an archive first.",
        versionRequired: "Enter a version.",
        versionExists: "This Package version already exists and versions are immutable. Choose a different version.",
        download: "Download",
        downloadDone: "Download started.",
        details: "Details",
        detailsTitle: "Package details",
        metadataLabel: "Metadata",
        versionHistory: "Version history",
        versionHistoryEmpty: "This Package has no versions yet.",
        manage: "Manage",
        manageTitle: "Edit Package",
        statusChange: "Status",
        save: "Save",
        saved: "Package updated.",
        versionStatusSaved: "Version status updated.",
        deleteVersion: "Delete version",
        deleteVersionTitle: "Confirm version deletion",
        deleteVersionBody: "The archive stops being downloadable immediately; the version number can be uploaded again.",
        versionDeleted: "Version deleted.",
        deletePackage: "Delete Package",
        deletePackageTitle: "Confirm Package deletion",
        deletePackageBody: "The Package and all of its versions disappear; version numbers can be uploaded again.",
        packageDeleted: "Package deleted.",
        deleteConfirm: "Delete",
        resumeDraft: "An unfinished upload draft was found.",
        resumeNeedsFile: "Select the same archive again to continue the upload.",
        resumeConfirm: "The archive is uploaded; publication can be confirmed.",
        resumeMismatch: "Select the archive that belongs to this draft.",
        discardDraft: "Discard draft",
    },
};

const {locale, t: baseT} = useLocale();
const authorization = useAuthorization();
const {selected} = useWorkspaceContext();
const request = useRequest<PackagePage>();
const toast = useToast();
const permissionFailure = ref<RequestFailure | null>(null);

const localeName = computed(() => locale.value === "zh" ? "zh-CN" : "en-US");
const workspace = computed(() => selected.value);
const node = computed(() => {
    if (!workspace.value) return "";
    return workspace.value.workspace_kind === "system"
        ? "team.system.packages"
        : `team.${workspace.value.team_id}.packages`;
});
const systemWorkspace = computed(() => workspace.value?.workspace_kind === "system");
const canRead = computed(() => node.value ? authorization.can(`${node.value}.read`) : false);
const uploadNode = computed(() => workspace.value
    ? `team.${workspace.value.team_id}.package_uploads`
    : "");
// 上传是一个 create + confirm 的完整流程，两个动作都要有权限才显示入口。
const canUpload = computed(() =>
    !systemWorkspace.value
    && Boolean(uploadNode.value)
    && authorization.can(`${uploadNode.value}.create`)
    && authorization.can(`${uploadNode.value}.confirm`),
);
const canCreate = computed(() =>
    !systemWorkspace.value
    && Boolean(node.value)
    && authorization.can(`${node.value}.create`),
);
// 版本状态变更、删除与元数据编辑沿用同一套授权：节点上的 `confirm` 与 `manage` 缺一不可。
// 动作的落点按行判定：Team 工作区就是当前节点；System 目录里每行属于别的 Team，
// 必须打到该包所属 Team 的 `packages` 节点（有那个 Team 的权限才显示入口）。
// 上传资源按 Team 注册（modules/packages/resources.py），系统目录只读：说明入口在哪，
// 避免用户在只读清单上找上传按钮。
const uploadScopeNote = computed(() =>
    !canUpload.value && systemWorkspace.value ? pageT("uploadScopeNote") : "",
);
const pageTitleDescription = computed(() =>
    systemWorkspace.value ? pageT("catalogDescription") : pageT("description"),
);

function rowActionNode(row: PackageRow): string {
    if (!systemWorkspace.value) return node.value;
    const teamId = row.team_id ?? "";
    return teamId ? `team.${teamId}.packages` : "";
}

function canManageRow(row: PackageRow): boolean {
    const target = rowActionNode(row);
    return Boolean(target) && authorization.can(`${target}.manage`);
}

function canChangeVersionStatusFor(row: PackageRow): boolean {
    const target = rowActionNode(row);
    return canManageRow(row) && Boolean(target) && authorization.can(`${target}.confirm`);
}

const page = ref(1);
const pageSize = 50;
const pageCount = computed(() => Math.max(
    1,
    Math.ceil((request.data.value?.total ?? 0) / pageSize),
));
// 平台目录跨 Team，行内要看得见归属；Team 工作区的归属就是当前 Team。
const packageColumns = computed<Array<{key: keyof PackageRow; label: string}>>(() => {
    const columns: Array<{key: keyof PackageRow; label: string}> = [
        {key: "package_id", label: pageT("package")},
    ];
    if (systemWorkspace.value) {
        columns.push({key: "team_id", label: pageT("team")});
    }
    columns.push(
        {key: "name", label: pageT("name")},
        {key: "version_count", label: pageT("versionCount")},
        {key: "status", label: pageT("visibility")},
        {key: "updated_at", label: pageT("updatedAt")},
        {key: "actions", label: pageT("actions")},
    );
    return columns;
});

const createDialogOpen = ref(false);
const createMod = ref("");
const createMetadata = ref<Record<string, unknown>>({});
const createPending = ref(false);
const createError = ref<string | null>(null);
const createPackageId = computed(() => {
    const mod = createMod.value.trim().toLowerCase();
    const teamId = workspace.value?.team_id ?? "";
    return teamId && mod ? `${teamId}.${mod}` : "";
});

const uploadDialogOpen = ref(false);
const uploadPackageId = ref("");
const uploadVersion = ref("");
const selectedFile = ref<File | null>(null);
// null = 当前没有已判形态的文件。
const payloadKind = ref<PayloadKind | null>(null);
const uploadPhase = ref<"idle" | "preparing" | "sending" | "confirming" | "done">("idle");
// 0-100 字节级上传进度（仅 sending 阶段有意义）。
const uploadProgress = ref(0);
const uploadError = ref<string | null>(null);
const restoredUpload = ref<UploadResumeDraft | null>(null);

const detailsDialogOpen = ref(false);
const detailsPackageId = ref("");
const detailsPackage = computed(() => request.data.value?.items
    .find((item) => item.package_id === detailsPackageId.value) ?? null);
const detailsVersions = computed<PackageVersionRow[]>(() => [
    ...(detailsPackage.value?.versions ?? []),
].reverse());
const versionStatusPending = ref(false);

const manageDialogOpen = ref(false);
const managePackageId = ref("");
const managePackage = computed(() => request.data.value?.items
    .find((item) => item.package_id === managePackageId.value) ?? null);
const metadataModel = ref<Record<string, unknown>>({});
const packageStatus = ref("published");
const managePending = ref(false);
const manageError = ref<string | null>(null);

let stopBroadcastSubscription: (() => void) | null = null;
let reloadQueued = false;
let loadInProgress = false;

const phase = computed<RequestPhase>(() => {
    if (!workspace.value) return "empty";
    if (permissionFailure.value) return "forbidden";
    if (authorization.phase.value !== "success") return authorization.phase.value;
    if (!canRead.value) return "forbidden";
    return request.phase.value;
});

function pageT(key: string) {
    return messages[locale.value][key] ?? messages.en[key] ?? key;
}

function asObject(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function asNumber(value: unknown): number | undefined {
    return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

// 归档结构、元数据不合规等校验失败的具体原因由服务端给出（例如
// `archive entry is not Mods/<name>.dll`），通用文案会把它丢掉。
function packageFailure(response: TransportResponse): RequestFailure | null {
    const failure = responseFailure(response);
    if (!failure) return null;
    const detail = String(response.error?.message ?? "").trim();
    if (failure.kind === "validation" && detail) {
        return new RequestFailure(detail, "validation", failure.code);
    }
    return failure;
}

function parseVersion(value: unknown): PackageVersionRow | null {
    const item = asObject(value);
    const version = typeof item.version === "string" ? item.version : "";
    if (!version) return null;
    return {
        version,
        status: typeof item.status === "string" ? item.status : undefined,
        archive_size: asNumber(item.archive_size),
        archive_digest: typeof item.archive_digest === "string" ? item.archive_digest : undefined,
        created_at: asNumber(item.created_at),
    };
}

// 读接口一行一个包：元数据与可见性属于包，版本历史挂在下面。
function parsePackageRows(values: unknown[]): PackageRow[] {
    return values.flatMap((value) => {
        const entry = asObject(value);
        const packageId = typeof entry.package_id === "string" ? entry.package_id : "";
        if (!packageId) return [];
        const versions = Array.isArray(entry.versions)
            ? entry.versions.flatMap((item) => parseVersion(item) ?? [])
            : [];
        return [{
            package_id: packageId,
            team_id: typeof entry.team_id === "string" ? entry.team_id : undefined,
            name: typeof entry.name === "string" ? entry.name : "",
            status: typeof entry.status === "string" ? entry.status : undefined,
            metadata: asObject(entry.metadata),
            created_at: asNumber(entry.created_at),
            updated_at: asNumber(entry.updated_at),
            version_count: versions.length,
            versions,
        }];
    });
}

function parsePackages(response: TransportResponse): PackagePage {
    const failure = responseFailure(response);
    if (failure) throw failure;
    const values = response.data?.packages;
    if (!Array.isArray(values)) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    const items = parsePackageRows(values);
    const total = Number(response.data?.total ?? items.length);
    const limit = Number(response.data?.limit ?? pageSize);
    const offset = Number(response.data?.offset ?? 0);
    if (!Number.isInteger(total) || total < 0 || !Number.isInteger(limit)
        || !Number.isInteger(offset) || limit < 1 || offset < 0) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    return {items, total, limit, offset};
}

async function load() {
    permissionFailure.value = null;
    if (!workspace.value) return;
    if (loadInProgress) {
        reloadQueued = true;
        return;
    }
    loadInProgress = true;
    try {
        await authorization.ensure(transport.selectedTeamId);
        if (!canRead.value) {
            permissionFailure.value = new RequestFailure(pageT("permission"), "forbidden", "permission_denied");
            return;
        }
        await request.run(async (signal) => {
            const response = await transport.request({
                action: "read",
                node: node.value,
                data: {
                    limit: pageSize,
                    offset: (page.value - 1) * pageSize,
                },
            });
            if (signal.aborted) {
                throw new RequestFailure(baseT("common.requestCancelled"), "error", "cancelled");
            }
            return parsePackages(response);
        }, {isEmpty: (result) => result.items.length === 0});
    } catch (cause) {
        if (cause instanceof RequestFailure && cause.code === "permission_denied") {
            permissionFailure.value = cause;
        }
    } finally {
        loadInProgress = false;
        if (reloadQueued) {
            reloadQueued = false;
            void load();
        }
    }
}

// 下载既可由 Team 级 `packages.download` 授权，也可由具体 Package 的 `.download` 授权。
function packageNode(packageId: string) {
    const separator = packageId.indexOf(".");
    const mod = separator >= 0 ? packageId.slice(separator + 1) : "";
    if (!workspace.value || !mod) return "";
    return `team.${workspace.value.team_id}.${mod.replace(/\./g, "_")}`;
}

function canDownloadVersion(packageId: string) {
    if (!node.value || systemWorkspace.value) return false;
    const specific = packageNode(packageId);
    return authorization.can(`${node.value}.download`)
        || (Boolean(specific) && authorization.can(`${specific}.download`));
}

function formatSize(value?: number) {
    return value === undefined ? baseT("common.emptyValue") : `${value} ${pageT("bytes")}`;
}

function shortDigest(value?: string) {
    return value ? `${value.slice(0, 12)}...` : baseT("common.emptyValue");
}

function statusLabel(status?: string) {
    if (status === "published") return pageT("publishedState");
    if (status === "disabled") return pageT("disabled");
    if (status === "unpublished") return pageT("unpublished");
    return baseT("common.emptyValue");
}

function openCreate() {
    if (!canCreate.value || createPending.value) return;
    createMod.value = "";
    // 新建的包给一份与公开示例同形的文档起步：条目要求安装规则，空文档存不下去。
    createMetadata.value = newExampleMetadata();
    createError.value = null;
    createDialogOpen.value = true;
}

function closeCreate() {
    if (createPending.value) return;
    createDialogOpen.value = false;
}

async function createPackage() {
    if (!canCreate.value || createPending.value || !workspace.value) return;
    const mod = createMod.value.trim().toLowerCase();
    if (!mod) {
        createError.value = pageT("createModRequired");
        return;
    }
    createError.value = null;
    createPending.value = true;
    try {
        const response = await transport.request({
            action: "create",
            node: node.value,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {mod, metadata: {...createMetadata.value}},
        });
        const failure = packageFailure(response);
        if (failure) throw failure;
        createDialogOpen.value = false;
        createMod.value = "";
        createMetadata.value = {};
        toast.push(pageT("createDone"), "success");
        await load();
    } catch (cause) {
        createError.value = cause instanceof Error ? cause.message : String(cause);
    } finally {
        createPending.value = false;
    }
}

function uploadDraftKey(packageId: string) {
    return `sprocket.package.uploadDraft.v1.${packageId}`;
}

function digestToHex(buffer: ArrayBuffer) {
    return [...new Uint8Array(buffer)]
        .map((value) => value.toString(16).padStart(2, "0"))
        .join("");
}

function readStoredUploadDraft(packageId: string) {
    try {
        const parsed = JSON.parse(localStorage.getItem(uploadDraftKey(packageId)) ?? "null") as Partial<UploadResumeDraft> | null;
        if (!parsed || typeof parsed !== "object") return null;
        if (parsed.packageId !== packageId || !parsed.version || !parsed.createKey || !parsed.confirmKey) {
            return null;
        }
        if (typeof parsed.size !== "number" || typeof parsed.fileName !== "string") return null;
        if (typeof parsed.expiresAt === "number" && parsed.expiresAt * 1000 <= Date.now()) {
            localStorage.removeItem(uploadDraftKey(packageId));
            return null;
        }
        return parsed as UploadResumeDraft;
    } catch {
        localStorage.removeItem(uploadDraftKey(packageId));
        return null;
    }
}

function storeUploadDraft(draft: UploadResumeDraft) {
    localStorage.setItem(uploadDraftKey(draft.packageId), JSON.stringify(draft));
    restoredUpload.value = draft;
}

function discardUploadDraft(packageId: string) {
    localStorage.removeItem(uploadDraftKey(packageId));
    restoredUpload.value = null;
}

function openUpload(row: PackageRow) {
    if (!canUpload.value || uploadPhase.value !== "idle") return;
    const draft = readStoredUploadDraft(row.package_id);
    restoredUpload.value = draft;
    uploadPackageId.value = row.package_id;
    uploadVersion.value = draft?.version ?? "";
    selectedFile.value = null;
    payloadKind.value = null;
    uploadError.value = null;
    uploadDialogOpen.value = true;
}

function closeUpload() {
    if (uploadPhase.value !== "idle" && uploadPhase.value !== "done") return;
    uploadDialogOpen.value = false;
    uploadPackageId.value = "";
    uploadVersion.value = "";
    selectedFile.value = null;
    payloadKind.value = null;
    restoredUpload.value = null;
    uploadPhase.value = "idle";
    uploadProgress.value = 0;
}

function fileNameOf(file: File) {
    return file.name.split(/[\\/]/).pop() ?? file.name;
}

function isDllFile(file: File) {
    return fileNameOf(file).toLowerCase().endsWith(".dll");
}

// 与服务端 `packages/files.py` 的魔数判断逐字一致：`PK` 前缀是 zip 归档，其余是单个 DLL。
async function detectPayloadKind(file: File): Promise<PayloadKind> {
    const header = new Uint8Array(await file.slice(0, 2).arrayBuffer());
    const isArchive = header[0] === 0x50 && header[1] === 0x4b;
    return isArchive ? "archive" : "single";
}

async function chooseFile(event: Event) {
    const input = event.target;
    if (!(input instanceof HTMLInputElement)) return;
    const file = input.files?.[0] ?? null;
    selectedFile.value = file;
    payloadKind.value = null;
    uploadError.value = null;
    if (!file) return;
    let kind: PayloadKind;
    try {
        kind = await detectPayloadKind(file);
    } catch (cause) {
        uploadError.value = cause instanceof Error ? cause.message : String(cause);
        return;
    }
    // 读取期间用户可能又选了别的文件：结果只对当前文件生效。
    if (selectedFile.value !== file) return;
    payloadKind.value = kind;
    if (kind === "single" && !isDllFile(file)) {
        uploadError.value = pageT("singleNotDll");
    }
}

function removeRestoredUpload() {
    if (uploadPhase.value !== "idle") return;
    discardUploadDraft(uploadPackageId.value);
    uploadVersion.value = "";
    selectedFile.value = null;
    payloadKind.value = null;
    uploadError.value = null;
}

function fileMatchesDraft(file: File, draft: UploadResumeDraft) {
    return file.name === draft.fileName
        && file.size === draft.size
        && file.lastModified === draft.lastModified;
}

function uploadWithProgress(url: string, file: File, contentType: string): Promise<boolean> {
    return new Promise((resolve) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", url);
        xhr.setRequestHeader("Content-Type", contentType);
        xhr.upload.onprogress = (event) => {
            if (event.lengthComputable && event.total > 0) {
                uploadProgress.value = Math.min(
                    100,
                    Math.round((event.loaded / event.total) * 100),
                );
            }
        };
        xhr.onload = () => resolve(xhr.status >= 200 && xhr.status < 300);
        xhr.onerror = () => resolve(false);
        xhr.send(file);
    });
}

async function upload() {
    const file = selectedFile.value;
    const packageIdValue = uploadPackageId.value;
    const confirmOnlyDraft = restoredUpload.value?.uploadedAt && restoredUpload.value.sha256
        ? restoredUpload.value
        : null;
    if (!workspace.value || !canUpload.value || !packageIdValue) {
        uploadError.value = pageT("noFile");
        return;
    }
    if (!file && !confirmOnlyDraft) {
        uploadError.value = pageT("noFile");
        return;
    }
    // 单个非 DLL 文件没有可用的安装目标，服务端一定拒绝：在创建上传草稿前就挡下。
    if (file && payloadKind.value === "single" && !isDllFile(file)) {
        uploadError.value = pageT("singleNotDll");
        return;
    }
    try {
        uploadError.value = null;
        uploadProgress.value = 0;
        let draft = restoredUpload.value;
        if (draft && draft.packageId !== packageIdValue) draft = null;
        if (draft && file && !fileMatchesDraft(file, draft)) {
            throw new RequestFailure(pageT("resumeMismatch"), "validation", "invalid_request");
        }
        if (!draft) {
            if (!file) throw new RequestFailure(pageT("noFile"), "validation", "invalid_request");
            const version = uploadVersion.value.trim();
            if (!version) {
                throw new RequestFailure(pageT("versionRequired"), "validation", "invalid_request");
            }
            draft = {
                packageId: packageIdValue,
                version,
                size: file.size,
                contentType: file.type
                    || (payloadKind.value === "single" ? "application/octet-stream" : "application/zip"),
                fileName: file.name,
                lastModified: file.lastModified,
                createKey: crypto.randomUUID(),
                confirmKey: crypto.randomUUID(),
            };
            storeUploadDraft(draft);
        }
        if (!draft) throw new RequestFailure(pageT("noFile"), "validation", "invalid_request");
        if (!draft.version.trim()) {
            throw new RequestFailure(pageT("versionRequired"), "validation", "invalid_request");
        }
        // 版本不可变：本页已加载的同一 Package 已有该版本时阻止创建草稿；
        // 后端 confirm 仍以唯一约束兜底拒绝。
        if (!draft.uploadId) {
            const draftPackageId = draft.packageId;
            const draftVersion = draft.version;
            const knownImportedVersion = request.data.value
                ? request.data.value.items.some((item) =>
                    item.package_id === draftPackageId
                    && item.versions.some((version) => version.version === draftVersion))
                : false;
            if (knownImportedVersion) {
                throw new RequestFailure(pageT("versionExists"), "validation", "invalid_request");
            }
        }
        if (!draft.uploadId || !draft.uploadUrl) {
            uploadPhase.value = "preparing";
            const created = await transport.request({
                action: "create",
                node: uploadNode.value,
                headers: {"Idempotency-Key": draft.createKey},
                data: {
                    package_id: draft.packageId,
                    version: draft.version,
                    size: draft.size,
                    content_type: draft.contentType,
                },
            });
            const createFailure = packageFailure(created);
            if (createFailure) throw createFailure;
            const upload = created.data?.upload as {upload_url?: string; object_key?: string} | undefined;
            const uploadId = String(created.data?.upload_id ?? "");
            if (!upload?.upload_url || !uploadId) {
                throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
            }
            draft = {
                ...draft,
                uploadId,
                uploadUrl: upload.upload_url,
                expiresAt: typeof created.data?.expires_at === "number" ? created.data.expires_at : draft.expiresAt,
            };
            storeUploadDraft(draft);
        }
        if (!draft.uploadedAt) {
            if (!file) throw new RequestFailure(pageT("noFile"), "validation", "invalid_request");
            const uploadUrlValue = draft.uploadUrl;
            if (!uploadUrlValue) {
                throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
            }
            uploadPhase.value = "sending";
            const sha256 = draft.sha256 ?? digestToHex(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()));
            draft = {...draft, sha256};
            storeUploadDraft(draft);
            const configuredOrigin = String(import.meta.env.VITE_API_ORIGIN ?? "").trim();
            const uploadUrl = new URL(uploadUrlValue, configuredOrigin || window.location.origin);
            // 用 XMLHttpRequest 获取字节级上传进度（fetch 无可靠 upload 进度事件）。
            const sent = await uploadWithProgress(uploadUrl.toString(), file, draft.contentType);
            if (!sent) {
                throw new RequestFailure(baseT("common.archiveUploadFailed"), "server-error", "upload_failed");
            }
            draft = {...draft, uploadedAt: Date.now()};
            storeUploadDraft(draft);
        }

        uploadPhase.value = "confirming";
        const confirmed = await transport.request({
            action: "confirm",
            node: uploadNode.value,
            headers: {"Idempotency-Key": draft.confirmKey},
            data: {
                upload_id: draft.uploadId,
                sha256: draft.sha256,
            },
        });
        const confirmFailure = packageFailure(confirmed);
        if (confirmFailure) throw confirmFailure;
        discardUploadDraft(draft.packageId);
        uploadPhase.value = "done";
        toast.push(pageT("uploadDone"), "success");
        await load();
    } catch (cause) {
        uploadPhase.value = "idle";
        uploadError.value = cause instanceof Error ? cause.message : String(cause);
        toast.push(uploadError.value, "error");
    }
}

const uploadStatus = computed(() => {
    if (uploadPhase.value === "preparing") return pageT("uploadPreparing");
    if (uploadPhase.value === "sending") return pageT("uploadSending");
    if (uploadPhase.value === "confirming") return pageT("uploadConfirming");
    if (uploadPhase.value === "done") return pageT("uploadDone");
    return "";
});

async function downloadVersion(packageId: string, version: string) {
    if (!canDownloadVersion(packageId) || !workspace.value) return;
    try {
        const response = await transport.request({
            action: "download",
            node: node.value,
            data: {package_id: packageId, version},
        });
        const failure = packageFailure(response);
        if (failure) throw failure;
        const token = String(response.data?.token ?? "");
        const downloadPath = String(response.data?.download_path ?? "");
        if (!token || !downloadPath) {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        const configuredOrigin = String(import.meta.env.VITE_API_ORIGIN ?? "").trim();
        const origin = configuredOrigin || window.location.origin;
        const url = new URL(downloadPath, origin);
        url.searchParams.set("version", version);
        url.searchParams.set("token", token);
        const anchor = document.createElement("a");
        anchor.href = url.toString();
        anchor.download = `${packageId}-${version}.mod`;
        anchor.click();
        toast.push(pageT("downloadDone"), "success");
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    }
}

function openDetails(row: PackageRow) {
    detailsPackageId.value = row.package_id;
    detailsDialogOpen.value = true;
}

function closeDetails() {
    detailsDialogOpen.value = false;
    detailsPackageId.value = "";
}

async function setVersionStatus(row: PackageVersionRow, status: string): Promise<boolean> {
    const item = detailsPackage.value;
    const target = item ? rowActionNode(item) : "";
    if (!item || !target || !canChangeVersionStatusFor(item) || versionStatusPending.value) return false;
    versionStatusPending.value = true;
    try {
        const preview = await transport.request({
            action: "manage",
            node: target,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {package_id: item.package_id, version: row.version, status, preview: true},
        });
        const previewFailure = packageFailure(preview);
        if (previewFailure) throw previewFailure;
        const confirmation = await transport.request({
            action: "confirm",
            node: target,
            data: {package_id: item.package_id, version: row.version},
        });
        const confirmationFailure = packageFailure(confirmation);
        if (confirmationFailure) throw confirmationFailure;
        const token = confirmation.data?.confirmation_token;
        if (typeof token !== "string" || !token) {
            throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
        }
        const response = await transport.request({
            action: "manage",
            node: target,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {
                package_id: item.package_id,
                version: row.version,
                metadata: {},
                status,
                confirmation_token: token,
            },
        });
        const failure = packageFailure(response);
        if (failure) throw failure;
        toast.push(pageT("versionStatusSaved"), "success");
        await load();
        return true;
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
        return false;
    } finally {
        versionStatusPending.value = false;
    }
}

async function changeVersionStatus(row: PackageVersionRow, event: Event) {
    const target = event.target;
    if (!(target instanceof HTMLSelectElement)) return;
    const previous = row.status ?? "published";
    if (target.value === previous) return;
    const applied = await setVersionStatus(row, target.value);
    if (!applied) target.value = previous;
}

function openManage(row: PackageRow) {
    if (!canManageRow(row) || managePending.value) return;
    managePackageId.value = row.package_id;
    metadataModel.value = {...row.metadata};
    packageStatus.value = row.status ?? "published";
    manageError.value = null;
    manageDialogOpen.value = true;
}

function closeManage() {
    if (managePending.value) return;
    manageDialogOpen.value = false;
    managePackageId.value = "";
}

async function savePackage() {
    const item = managePackage.value;
    const target = item ? rowActionNode(item) : "";
    if (!item || !target || !canManageRow(item) || managePending.value) return;
    const metadata: Record<string, unknown> = {...metadataModel.value};
    const name = typeof metadata.name === "string" ? metadata.name.trim() : "";
    manageError.value = null;
    managePending.value = true;
    try {
        const response = await transport.request({
            action: "manage",
            node: target,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {
                package_id: item.package_id,
                metadata,
                ...(name ? {name} : {}),
                ...(packageStatus.value !== (item.status ?? "published")
                    ? {status: packageStatus.value}
                    : {}),
            },
        });
        const failure = packageFailure(response);
        if (failure) throw failure;
        manageDialogOpen.value = false;
        managePackageId.value = "";
        toast.push(pageT("saved"), "success");
        await load();
    } catch (cause) {
        manageError.value = cause instanceof Error ? cause.message : String(cause);
    } finally {
        managePending.value = false;
    }
}

// 破坏性变更统一走"确认令牌"：`confirm` 动作签发令牌，`manage` 带 `delete` 与令牌执行。
// 令牌目标由版本是否存在决定（见 modules/packages/resources.py 的 `confirmation`）。
async function issueConfirmation(target: string, data: Record<string, unknown>) {
    const confirmation = await transport.request({action: "confirm", node: target, data});
    const failure = packageFailure(confirmation);
    if (failure) throw failure;
    const token = confirmation.data?.confirmation_token;
    if (typeof token !== "string" || !token) {
        throw new RequestFailure(pageT("invalid"), "validation", "invalid_response");
    }
    return token;
}

const versionDeleteTarget = ref<{packageId: string; version: PackageVersionRow} | null>(null);
const versionDeletePending = ref(false);

function openVersionDelete(row: PackageVersionRow) {
    const item = detailsPackage.value;
    if (!item || !canChangeVersionStatusFor(item) || versionDeletePending.value) return;
    versionDeleteTarget.value = {packageId: item.package_id, version: row};
}

function closeVersionDelete() {
    if (versionDeletePending.value) return;
    versionDeleteTarget.value = null;
}

async function deleteVersion() {
    const pending = versionDeleteTarget.value;
    const item = detailsPackage.value;
    const target = item ? rowActionNode(item) : "";
    if (!pending || !item || !target || !canChangeVersionStatusFor(item) || versionDeletePending.value) return;
    versionDeletePending.value = true;
    try {
        const token = await issueConfirmation(target, {
            package_id: pending.packageId,
            version: pending.version.version,
        });
        const response = await transport.request({
            action: "manage",
            node: target,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {
                package_id: pending.packageId,
                version: pending.version.version,
                delete: true,
                confirmation_token: token,
            },
        });
        const failure = packageFailure(response);
        if (failure) throw failure;
        versionDeleteTarget.value = null;
        toast.push(pageT("versionDeleted"), "success");
        await load();
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        versionDeletePending.value = false;
    }
}

const packageDeleteTarget = ref<PackageRow | null>(null);
const packageDeletePending = ref(false);

function openPackageDelete() {
    const item = managePackage.value;
    if (!item || !canManageRow(item) || packageDeletePending.value) return;
    packageDeleteTarget.value = item;
}

function closePackageDelete() {
    if (packageDeletePending.value) return;
    packageDeleteTarget.value = null;
}

async function deletePackage() {
    const row = packageDeleteTarget.value;
    const target = row ? rowActionNode(row) : "";
    if (!row || !target || !canChangeVersionStatusFor(row) || packageDeletePending.value) return;
    packageDeletePending.value = true;
    try {
        const token = await issueConfirmation(target, {package_id: row.package_id});
        const response = await transport.request({
            action: "manage",
            node: target,
            headers: {"Idempotency-Key": crypto.randomUUID()},
            data: {package_id: row.package_id, delete: true, confirmation_token: token},
        });
        const failure = packageFailure(response);
        if (failure) throw failure;
        packageDeleteTarget.value = null;
        manageDialogOpen.value = false;
        managePackageId.value = "";
        if (detailsPackageId.value === row.package_id) closeDetails();
        toast.push(pageT("packageDeleted"), "success");
        await load();
    } catch (cause) {
        toast.push(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
        packageDeletePending.value = false;
    }
}

onMounted(load);
onMounted(() => {
    stopBroadcastSubscription = transport.subscribe((message) => {
        if (message.request_id) return;
        if (message.kind !== "resource.changed") return;
        if (!workspace.value) return;
        const teamId = workspace.value.team_id;
        const nodePrefix = `team.${teamId}.`;
        const node = String(message.node ?? "");
        if (!node.startsWith(nodePrefix)) return;
        const isPackageChange =
            node.endsWith(".packages")
            || node.includes(".packages.")
            || node.endsWith(".package_uploads");
        if (!isPackageChange) return;
        void load();
    });
});
onBeforeUnmount(() => {
    stopBroadcastSubscription?.();
    stopBroadcastSubscription = null;
});
watch(() => workspace.value?.team_id, (current, previous) => {
    if (current === previous) return;
    page.value = 1;
    void load();
});

function previousPage() {
    if (page.value > 1) {
        page.value -= 1;
        void load();
    }
}

function nextPage() {
    if (page.value < pageCount.value) {
        page.value += 1;
        void load();
    }
}
</script>

<template>
    <div class="packages-page">
        <PageHeader
            :eyebrow="pageT('title')"
            :title="pageT('title')"
            :description="pageTitleDescription"
            :refresh-label="pageT('refresh')"
            :refreshing="request.loading.value"
            @refresh="load"
        >
            <template #actions>
                <UiButton
                    v-if="canCreate"
                    variant="primary"
                    :disabled="createPending"
                    @click="openCreate"
                >
                    {{ pageT("newPackage") }}
                </UiButton>
            </template>
        </PageHeader>

        <p v-if="uploadScopeNote" class="scope-note">{{ uploadScopeNote }}</p>

        <RequestState
            :phase="phase"
            :message="!workspace ? pageT('noWorkspace') : phase === 'forbidden' ? pageT('permission') : null"
            @retry="load"
            @cancel="request.cancel"
        />

        <section v-if="request.data.value && phase !== 'forbidden'" class="table-section">
            <UiDataTable
                :columns="packageColumns"
                :rows="request.data.value.items"
            >
                <template #package_id="{row}">
                    <strong>{{ row.package_id }}</strong>
                </template>
                <template #team_id="{row}">
                    {{ row.team_id || baseT("common.emptyValue") }}
                </template>
                <template #name="{row}">
                    {{ row.name || baseT("common.emptyValue") }}
                </template>
                <template #version_count="{row}">
                    {{ row.version_count }}
                </template>
                <template #status="{row}">
                    <span :class="['status', `is-${row.status ?? 'unpublished'}`]">
                        {{ statusLabel(row.status) }}
                    </span>
                </template>
                <template #updated_at="{row}">
                    {{ formatTimestamp(row.updated_at, localeName) }}
                </template>
                <template #actions="{row}">
                    <div class="row-actions">
                        <UiButton variant="ghost" @click="openDetails(row)">
                            {{ pageT("details") }}
                        </UiButton>
                        <UiButton
                            v-if="canUpload"
                            variant="ghost"
                            :disabled="uploadPhase !== 'idle'"
                            @click="openUpload(row)"
                        >
                            {{ pageT("upload") }}
                        </UiButton>
                        <UiButton
                            v-if="canManageRow(row)"
                            variant="ghost"
                            :disabled="managePending"
                            @click="openManage(row)"
                        >
                            {{ pageT("manage") }}
                        </UiButton>
                    </div>
                </template>
            </UiDataTable>
            <div v-if="pageCount > 1" class="pagination">
                <UiButton variant="ghost" :disabled="page === 1" @click="previousPage">
                    {{ baseT("common.previousPage") }}
                </UiButton>
                <span>{{ page }} / {{ pageCount }}</span>
                <UiButton variant="ghost" :disabled="page === pageCount" @click="nextPage">
                    {{ baseT("common.nextPage") }}
                </UiButton>
            </div>
        </section>

        <UiDialog :open="createDialogOpen" :title="pageT('createTitle')" :busy="createPending" @close="closeCreate">
            <div class="create-form">
                <label class="field">
                    <span>{{ pageT("createMod") }}</span>
                    <UiInput
                        v-model="createMod"
                        :aria-label="pageT('createMod')"
                        :placeholder="pageT('createModHint')"
                    />
                </label>
                <p class="field-preview">
                    <span>{{ pageT("packageId") }}</span>
                    <code>{{ createPackageId || baseT("common.emptyValue") }}</code>
                </p>
                <PackageMetadataJson v-model="createMetadata" :disabled="createPending"/>
                <p v-if="createError" class="form-error">{{ createError }}</p>
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="createPending" @click="closeCreate">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton variant="primary" :disabled="createPending" @click="createPackage">
                    {{ pageT("createSubmit") }}
                </UiButton>
            </template>
        </UiDialog>

        <UiDialog :open="uploadDialogOpen" :title="pageT('uploadTitle')" @close="closeUpload">
            <div class="upload-form">
                <p class="field-preview">
                    <span>{{ pageT("uploadPackage") }}</span>
                    <code>{{ uploadPackageId }}</code>
                </p>
                <UiInput
                    v-model="uploadVersion"
                    :aria-label="pageT('uploadVersion')"
                    :placeholder="pageT('versionHint')"
                    :disabled="Boolean(restoredUpload)"
                />
                <label class="file-field">
                    <span>{{ pageT("selectFile") }}</span>
                    <input
                        type="file"
                        :aria-label="pageT('selectFile')"
                        @change="chooseFile"
                    />
                </label>
                <div v-if="restoredUpload" class="upload-resume">
                    <div>
                        <strong>{{ pageT("resumeDraft") }}</strong>
                        <p>
                            {{ restoredUpload.uploadedAt && restoredUpload.sha256
                                ? pageT("resumeConfirm")
                                : pageT("resumeNeedsFile") }}
                        </p>
                        <code>{{ restoredUpload.fileName }} · {{ restoredUpload.packageId }}@{{ restoredUpload.version }}</code>
                    </div>
                    <UiButton variant="ghost" :disabled="uploadPhase !== 'idle'" @click="removeRestoredUpload">
                        {{ pageT("discardDraft") }}
                    </UiButton>
                </div>
                <p v-if="uploadStatus" class="upload-status">{{ uploadStatus }}</p>
                <div
                    v-if="uploadPhase === 'sending' && uploadProgress > 0"
                    class="upload-progress"
                    role="progressbar"
                    :aria-valuenow="uploadProgress"
                    aria-valuemin="0"
                    aria-valuemax="100"
                >
                    <span :style="{width: `${uploadProgress}%`}"></span>
                    <em>{{ uploadProgress }}%</em>
                </div>
                <p v-if="uploadError" class="form-error">{{ uploadError }}</p>
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="uploadPhase !== 'idle' && uploadPhase !== 'done'" @click="closeUpload">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton v-if="uploadPhase === 'idle'" variant="primary" @click="upload">
                    {{ pageT("startUpload") }}
                </UiButton>
            </template>
        </UiDialog>

        <UiDialog
            class="wide-dialog"
            :open="detailsDialogOpen"
            :title="pageT('detailsTitle')"
            @close="closeDetails"
        >
            <div v-if="detailsPackage" class="details-body">
                <dl class="package-details">
                    <div>
                        <dt>{{ pageT("packageId") }}</dt>
                        <dd><code>{{ detailsPackage.package_id }}</code></dd>
                    </div>
                    <div>
                        <dt>{{ pageT("team") }}</dt>
                        <dd>{{ detailsPackage.team_id || baseT("common.emptyValue") }}</dd>
                    </div>
                    <div>
                        <dt>{{ pageT("name") }}</dt>
                        <dd>{{ detailsPackage.name || baseT("common.emptyValue") }}</dd>
                    </div>
                    <div>
                        <dt>{{ pageT("status") }}</dt>
                        <dd>{{ statusLabel(detailsPackage.status) }}</dd>
                    </div>
                    <div>
                        <dt>{{ pageT("versionCount") }}</dt>
                        <dd>{{ detailsPackage.version_count }}</dd>
                    </div>
                    <div>
                        <dt>{{ pageT("createdAt") }}</dt>
                        <dd>{{ formatTimestamp(detailsPackage.created_at, localeName) }}</dd>
                    </div>
                    <div>
                        <dt>{{ pageT("updatedAt") }}</dt>
                        <dd>{{ formatTimestamp(detailsPackage.updated_at, localeName) }}</dd>
                    </div>
                    <div>
                        <dt>{{ pageT("metadataLabel") }}</dt>
                        <dd><pre>{{ JSON.stringify(detailsPackage.metadata ?? {}, null, 4) }}</pre></dd>
                    </div>
                </dl>
                <section class="version-history">
                    <h3>{{ pageT("versionHistory") }}</h3>
                    <p v-if="!detailsVersions.length" class="empty-note">
                        {{ pageT("versionHistoryEmpty") }}
                    </p>
                    <UiDataTable
                        v-else
                        :columns="[
                            {key: 'version', label: pageT('version')},
                            {key: 'status', label: pageT('status')},
                            {key: 'archive_size', label: pageT('size')},
                            {key: 'archive_digest', label: pageT('digest')},
                            {key: 'created_at', label: pageT('versionTime')},
                            {key: 'actions', label: pageT('actions')},
                        ]"
                        :rows="detailsVersions"
                    >
                        <template #version="{row}">
                            <strong>{{ row.version }}</strong>
                        </template>
                        <template #status="{row}">
                            <select
                                v-if="detailsPackage && canChangeVersionStatusFor(detailsPackage)"
                                class="status-select"
                                :aria-label="pageT('statusChange')"
                                :value="row.status ?? 'published'"
                                :disabled="versionStatusPending"
                                @change="changeVersionStatus(row, $event)"
                            >
                                <option value="published">{{ pageT("publishedState") }}</option>
                                <option value="disabled">{{ pageT("disabled") }}</option>
                                <option value="unpublished">{{ pageT("unpublished") }}</option>
                            </select>
                            <span v-else :class="['status', `is-${row.status ?? 'unpublished'}`]">
                                {{ statusLabel(row.status) }}
                            </span>
                        </template>
                        <template #archive_size="{row}">
                            {{ formatSize(row.archive_size) }}
                        </template>
                        <template #archive_digest="{row}">
                            <code :title="row.archive_digest">{{ shortDigest(row.archive_digest) }}</code>
                        </template>
                        <template #created_at="{row}">
                            {{ formatTimestamp(row.created_at, localeName) }}
                        </template>
                        <template #actions="{row}">
                            <div class="row-actions">
                                <UiButton
                                    v-if="canDownloadVersion(detailsPackage.package_id)"
                                    variant="ghost"
                                    @click="downloadVersion(detailsPackage.package_id, row.version)"
                                >
                                    {{ pageT("download") }}
                                </UiButton>
                                <UiButton
                                    v-if="canChangeVersionStatusFor(detailsPackage)"
                                    variant="danger"
                                    :disabled="versionDeletePending || versionStatusPending"
                                    @click="openVersionDelete(row)"
                                >
                                    {{ pageT("deleteVersion") }}
                                </UiButton>
                            </div>
                        </template>
                    </UiDataTable>
                </section>
            </div>
            <template #footer>
                <UiButton variant="ghost" @click="closeDetails">
                    {{ pageT("close") }}
                </UiButton>
            </template>
        </UiDialog>

        <UiDialog
            :open="manageDialogOpen"
            :title="pageT('manageTitle')"
            :busy="managePending"
            @close="closeManage"
        >
            <div class="manage-form">
                <p class="field-preview">
                    <span>{{ pageT("packageId") }}</span>
                    <code>{{ managePackageId }}</code>
                </p>
                <PackageMetadataJson v-model="metadataModel" :disabled="managePending"/>
                <label class="status-field">
                    <span>{{ pageT("statusChange") }}</span>
                    <select v-model="packageStatus" :aria-label="pageT('statusChange')">
                        <option value="published">{{ pageT("publishedState") }}</option>
                        <option value="disabled">{{ pageT("disabled") }}</option>
                        <option value="unpublished">{{ pageT("unpublished") }}</option>
                    </select>
                </label>
                <p v-if="manageError" class="form-error">{{ manageError }}</p>
            </div>
            <template #footer>
                <UiButton
                    v-if="managePackage && canChangeVersionStatusFor(managePackage)"
                    variant="danger"
                    :disabled="managePending || packageDeletePending"
                    @click="openPackageDelete"
                >
                    {{ pageT("deletePackage") }}
                </UiButton>
                <UiButton variant="ghost" :disabled="managePending" @click="closeManage">
                    {{ pageT("close") }}
                </UiButton>
                <UiButton variant="primary" :disabled="managePending" @click="savePackage">
                    {{ pageT("save") }}
                </UiButton>
            </template>
        </UiDialog>

        <UiDialog
            :open="versionDeleteTarget !== null"
            :title="pageT('deleteVersionTitle')"
            :busy="versionDeletePending"
            @close="closeVersionDelete"
        >
            <div v-if="versionDeleteTarget" class="confirm-form">
                <p>{{ pageT("deleteVersionBody") }}</p>
                <dl class="package-details">
                    <div>
                        <dt>{{ pageT("packageId") }}</dt>
                        <dd><code>{{ versionDeleteTarget.packageId }}</code></dd>
                    </div>
                    <div>
                        <dt>{{ pageT("version") }}</dt>
                        <dd><code>{{ versionDeleteTarget.version.version }}</code></dd>
                    </div>
                </dl>
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="versionDeletePending" @click="closeVersionDelete">
                    {{ baseT("common.cancel") }}
                </UiButton>
                <UiButton variant="danger" :disabled="versionDeletePending" @click="deleteVersion">
                    {{ pageT("deleteConfirm") }}
                </UiButton>
            </template>
        </UiDialog>

        <UiDialog
            :open="packageDeleteTarget !== null"
            :title="pageT('deletePackageTitle')"
            :busy="packageDeletePending"
            @close="closePackageDelete"
        >
            <div v-if="packageDeleteTarget" class="confirm-form">
                <p>{{ pageT("deletePackageBody") }}</p>
                <dl class="package-details">
                    <div>
                        <dt>{{ pageT("packageId") }}</dt>
                        <dd><code>{{ packageDeleteTarget.package_id }}</code></dd>
                    </div>
                    <div>
                        <dt>{{ pageT("versionCount") }}</dt>
                        <dd>{{ packageDeleteTarget.version_count }}</dd>
                    </div>
                </dl>
            </div>
            <template #footer>
                <UiButton variant="ghost" :disabled="packageDeletePending" @click="closePackageDelete">
                    {{ baseT("common.cancel") }}
                </UiButton>
                <UiButton variant="danger" :disabled="packageDeletePending" @click="deletePackage">
                    {{ pageT("deleteConfirm") }}
                </UiButton>
            </template>
        </UiDialog>
    </div>
</template>

<style scoped>
.packages-page {
    display: grid;
    gap: 24px;
}

.table-section {
    min-width: 0;
    padding: 2px 0;
}

.create-form,
.manage-form,
.upload-form,
.confirm-form {
    display: grid;
    gap: 14px;
}

.confirm-form > p {
    margin: 0;
    color: #c7d2fe;
}

.field,
.file-field,
.status-field {
    display: grid;
    gap: 6px;
    color: #9aa6b2;
    font-size: 12px;
}

.field-preview {
    display: grid;
    gap: 6px;
    margin: 0;
    color: #9aa6b2;
    font-size: 12px;
}

.field-preview code {
    overflow-wrap: anywhere;
}

.form-error {
    margin: 0;
    color: #ff9b9b;
}

.upload-form input[type="file"] {
    width: 100%;
    color: #9aa6b2;
}

.upload-resume {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 12px;
    align-items: center;
    padding: 12px;
    border: 1px solid rgba(116, 150, 255, 0.28);
    border-radius: 8px;
    background: rgba(74, 108, 255, 0.08);
}

.upload-resume p {
    margin: 4px 0;
    color: #c7d2fe;
}

.upload-resume code {
    display: block;
    overflow-wrap: anywhere;
    color: #9aa6b2;
}

.upload-status {
    margin: 0;
    color: #c8d5ff;
}

.upload-progress {
    position: relative;
    display: grid;
    place-items: center;
    min-height: 18px;
    border: 1px solid rgba(91, 140, 255, 0.45);
    border-radius: 9px;
    background: #101216;
    overflow: hidden;
}

.upload-progress span {
    position: absolute;
    inset: 0 auto 0 0;
    background: rgba(91, 140, 255, 0.32);
    transition: width 120ms linear;
}

.upload-progress em {
    position: relative;
    color: #c8d5ff;
    font-size: 11px;
    font-style: normal;
}

.row-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.pagination {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 12px;
    color: #9aa6b2;
}

/* 版本历史表有 7 列，默认弹窗宽度会把它们塞进横向滚动。 */
.packages-page :deep(.wide-dialog .dialog) {
    width: min(880px, 100%);
}

.status-field select,
.field select,
.status-select {
    min-height: 40px;
    padding: 0 10px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    background: #101216;
    color: #edf1f7;
    font: inherit;
}

.details-body {
    display: grid;
    gap: 20px;
}

.package-details {
    display: grid;
    gap: 14px;
    margin: 0;
}

.package-details div {
    display: grid;
    gap: 5px;
}

.package-details dt {
    color: #9aa6b2;
    font-size: 12px;
}

.package-details dd {
    margin: 0;
    overflow-wrap: anywhere;
}

.package-details pre {
    max-width: 100%;
    margin: 0;
    padding: 10px;
    overflow: auto;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    background: rgba(0, 0, 0, 0.16);
    white-space: pre-wrap;
}

.version-history {
    display: grid;
    gap: 10px;
}

.version-history h3 {
    margin: 0;
    font-size: 15px;
}

.empty-note {
    margin: 0;
    color: #9aa6b2;
}

.status {
    color: #9aa6b2;
}

.status.is-published {
    color: #78d88c;
}

.status.is-disabled {
    color: #ff9b9b;
}

code {
    color: #c8d5ff;
}

@media (max-width: 520px) {
    .upload-resume {
        grid-template-columns: minmax(0, 1fr);
    }
}
</style>
