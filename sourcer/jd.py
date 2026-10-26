"""Turn a pasted job description into a search profile (a roles.json-style role), without any AI service.

The JD is split into sections (requirements, responsibilities, nice-to-haves, about the company, benefits…) and
every line is weighted by where it sits, so a skill under "Requirements" counts more than one in the company blurb
and benefits or rejection criteria count for nothing. Skills come from a built-in dictionary that maps phrases to
GitHub signals: languages, repo topics, bio keywords and flagship repos whose contributors are worth checking.
GitHub's own repository search then adds active, popular projects for the top skills, so niche roles work too.
"""

import json
import re
from datetime import datetime, timedelta, timezone

from .github import GitHubError, log
from .score import DEFAULT_WEIGHTS

# -- dictionary ---------------------------------------------------------------
# match: phrases, matched case-insensitively on word boundaries; a leading "=" matches case-sensitively and
#        "re:" marks a regular expression.
# langs: GitHub language names. topics: GitHub repo topics (the first one is used for repo search).
# seeds: flagship repos for the skill; ones that don't exist (renamed, deleted) are dropped at analysis time.

SKILLS = {
    # mobile
    "iOS": dict(match=["ios", "iphone", "ipad", "uikit", "xcode", "apple platforms", "cocoa touch", "app store"],
                langs=["Swift", "Objective-C"], topics=["ios", "swift", "uikit", "iphone"],
                seeds=["Alamofire/Alamofire", "onevcat/Kingfisher", "pointfreeco/swift-composable-architecture",
                       "airbnb/lottie-ios", "SnapKit/SnapKit", "ReactiveX/RxSwift"]),
    "SwiftUI": dict(match=["swiftui", "combine framework", "swift concurrency"], langs=["Swift"],
                    topics=["swiftui", "swift", "ios"],
                    seeds=["pointfreeco/swift-composable-architecture", "pointfreeco/swift-snapshot-testing",
                           "SDWebImage/SDWebImageSwiftUI"]),
    "Android": dict(match=["android", "jetpack compose", "kotlin multiplatform", "kmp"], langs=["Kotlin", "Java"],
                    topics=["android", "jetpack-compose", "kotlin-android"],
                    seeds=["lysine-dev/okhttp", "lysine-dev/retrofit", "coil-kt/coil", "android/nowinandroid",
                           "JetBrains/compose-multiplatform", "bumptech/glide"]),
    "React Native": dict(match=["react native", "react-native", "expo"], langs=["TypeScript", "JavaScript"],
                         topics=["react-native", "expo"],
                         seeds=["react/react-native", "expo/expo", "software-mansion/react-native-reanimated",
                                "react-navigation/react-navigation"]),
    "Flutter": dict(match=["flutter", "=Dart"], langs=["Dart"], topics=["flutter", "dart"],
                    seeds=["flutter/flutter", "flutter/packages", "felangel/bloc", "rrousselGit/riverpod"]),
    "Mobile": dict(match=["mobile app", "mobile apps", "mobile engineer", "mobile development", "native apps",
                          "consumer app", "consumer apps"], langs=["Swift", "Kotlin"], topics=["mobile", "ios", "android"],
                   seeds=[]),

    # web
    "React": dict(match=["=React", "react.js", "reactjs", "react hooks", "redux"], langs=["TypeScript", "JavaScript"],
                  topics=["react", "reactjs", "react-hooks"],
                  seeds=["react/react", "TanStack/query", "pmndrs/zustand", "remix-run/react-router"]),
    "Next.js": dict(match=["next.js", "nextjs"], langs=["TypeScript"], topics=["nextjs", "react"],
                    seeds=["vercel/next.js", "vercel/swr"]),
    "Vue": dict(match=["vue", "vue.js", "vuejs", "nuxt"], langs=["TypeScript", "JavaScript", "Vue"],
                topics=["vue", "vuejs", "nuxt"], seeds=["vuejs/core", "nuxt/nuxt", "vitejs/vite"]),
    "Svelte": dict(match=["svelte", "sveltekit"], langs=["TypeScript", "Svelte"], topics=["svelte", "sveltekit"],
                   seeds=["sveltejs/svelte", "sveltejs/kit"]),
    "Angular": dict(match=["angular", "angularjs", "rxjs"], langs=["TypeScript"], topics=["angular"],
                    seeds=["angular/angular", "angular/components"]),
    "Frontend": dict(match=["frontend", "front-end", "front end", "css", "tailwind", "design system", "design systems",
                            "web ui", "user interfaces", "web app", "web apps", "browser"],
                     langs=["TypeScript", "JavaScript", "CSS"], topics=["frontend", "css", "design-system", "tailwindcss"],
                     seeds=["tailwindlabs/tailwindcss", "shadcn-ui/ui", "radix-ui/primitives", "storybookjs/storybook"]),
    "Web tooling": dict(match=["webpack", "vite", "esbuild", "bundler", "bundlers", "javascript tooling", "build tooling"],
                        langs=["TypeScript", "Rust", "Go"], topics=["bundler", "javascript", "build-tool"],
                        seeds=["vitejs/vite", "evanw/esbuild", "webpack/webpack", "swc-project/swc"]),
    "Node.js": dict(match=["node.js", "nodejs", "node", "express.js", "expressjs", "nestjs", "fastify"],
                    langs=["TypeScript", "JavaScript"], topics=["nodejs", "express", "nestjs"],
                    seeds=["nodejs/node", "nestjs/nest", "fastify/fastify", "expressjs/express"]),
    "Bun / Deno": dict(match=["bun", "deno"], langs=["TypeScript", "Zig", "Rust"], topics=["bun", "deno"],
                       seeds=["oven-sh/bun", "denoland/deno"]),
    "GraphQL": dict(match=["graphql", "apollo"], langs=["TypeScript", "Go"], topics=["graphql", "apollo"],
                    seeds=["graphql/graphql-js", "apollographql/apollo-client", "hasura/graphql-engine", "99designs/gqlgen"]),
    "Full-stack TypeScript": dict(match=["full stack", "full-stack", "fullstack", "trpc", "prisma", "drizzle", "supabase"],
                                  langs=["TypeScript"], topics=["fullstack", "typescript", "full-stack"],
                                  seeds=["vercel/next.js", "trpc/trpc", "prisma/orm", "drizzle-team/drizzle-orm",
                                         "supabase/supabase"]),
    "Desktop apps": dict(match=["electron", "tauri", "desktop app", "desktop apps", "desktop application"],
                         langs=["TypeScript", "Rust", "C++"], topics=["electron", "tauri", "desktop"],
                         seeds=["electron/electron", "tauri-apps/tauri"]),
    "WebAssembly": dict(match=["webassembly", "wasm"], langs=["Rust", "C++"], topics=["webassembly", "wasm"],
                        seeds=["bytecodealliance/wasmtime", "wasm-bindgen/wasm-bindgen", "wasmerio/wasmer",
                               "WebAssembly/binaryen"]),
    "3D / WebGL": dict(match=["three.js", "threejs", "webgl", "webgpu", "react three fiber", "3d web"],
                       langs=["TypeScript", "JavaScript"], topics=["threejs", "webgl", "webgpu"],
                       seeds=["mrdoob/three.js", "pmndrs/react-three-fiber", "BabylonJS/Babylon.js", "gfx-rs/wgpu"]),
    "Design engineering": dict(match=["design engineer", "product engineer", "ui engineer", "animation", "animations",
                                      "interaction design", "figma", "motion design", "pixel-perfect", "polish"],
                               langs=["TypeScript", "Swift"], topics=["animation", "ui", "design"],
                               seeds=["motiondivision/motion", "pmndrs/react-spring", "radix-ui/primitives",
                                      "shadcn-ui/ui"]),

    # backend & systems
    "Backend": dict(match=["backend", "back-end", "back end", "server-side", "apis", "api", "rest api", "rest apis",
                           "microservices", "web services", "services"],
                    langs=["Go", "Python", "Java", "TypeScript"], topics=["backend", "api", "microservices"], seeds=[]),
    "Distributed systems": dict(match=["distributed systems", "distributed system", "consensus", "raft", "paxos",
                                       "fault tolerance", "fault-tolerant", "replication", "high availability",
                                       "scalability", "at scale", "large scale", "large-scale"],
                                langs=["Go", "Rust", "Java", "C++"], topics=["distributed-systems", "consensus", "raft"],
                                seeds=["etcd-io/etcd", "cockroachdb/cockroach", "tikv/tikv", "hashicorp/raft",
                                       "apache/kafka", "temporalio/temporal"]),
    "Databases": dict(match=["database internals", "databases", "database", "storage engine", "storage engines",
                             "query engine", "query optimizer", "sql engine", "olap", "oltp", "data storage", "storage"],
                      langs=["C++", "Rust", "Go", "C"], topics=["database", "sql", "storage-engine"],
                      seeds=["duckdb/duckdb", "ClickHouse/ClickHouse", "cockroachdb/cockroach", "postgres/postgres",
                             "apache/datafusion", "facebook/rocksdb"]),
    "PostgreSQL": dict(match=["postgres", "postgresql", "psql"], langs=["C", "Go", "Rust"], topics=["postgresql", "postgres"],
                       seeds=["postgres/postgres", "supabase/supabase", "timescale/timescaledb", "pgvector/pgvector"]),
    "Redis & caching": dict(match=["redis", "caching", "memcached", "valkey"], langs=["C", "C++", "Go"],
                            topics=["redis", "cache"], seeds=["redis/redis", "valkey-io/valkey", "dragonflydb/dragonfly"]),
    "Streaming": dict(match=["kafka", "streaming", "stream processing", "event-driven", "event driven", "flink",
                             "pulsar", "kinesis", "pub/sub", "message queues", "rabbitmq"],
                      langs=["Java", "Go", "Scala", "Rust"], topics=["kafka", "streaming", "stream-processing"],
                      seeds=["apache/kafka", "apache/flink", "redpanda-data/redpanda", "apache/pulsar"]),
    "Rust async": dict(match=["tokio", "async rust", "axum", "hyper"], langs=["Rust"], topics=["rust", "async", "tokio"],
                       seeds=["tokio-rs/tokio", "tokio-rs/axum", "hyperium/hyper"]),
    "Python web": dict(match=["django", "flask", "fastapi", "pydantic", "celery"], langs=["Python"],
                       topics=["django", "fastapi", "flask"],
                       seeds=["django/django", "pallets/flask", "fastapi/fastapi", "pydantic/pydantic"]),
    "Ruby on Rails": dict(match=["rails", "ruby on rails", "ror", "sidekiq"], langs=["Ruby"], topics=["rails", "ruby"],
                          seeds=["rails/rails", "hotwired/turbo", "sidekiq/sidekiq"]),
    "Elixir / Phoenix": dict(match=["elixir", "phoenix", "liveview", "beam", "erlang/otp", "otp"], langs=["Elixir", "Erlang"],
                             topics=["elixir", "phoenix"],
                             seeds=["elixir-lang/elixir", "phoenixframework/phoenix", "phoenixframework/phoenix_live_view"]),
    "Java / Spring": dict(match=["spring boot", "spring framework", "java spring", "jvm", "netty", "hibernate"],
                          langs=["Java", "Kotlin"], topics=["spring-boot", "java", "jvm"],
                          seeds=["spring-projects/spring-boot", "spring-projects/spring-framework", "netty/netty"]),
    "gRPC & Protobuf": dict(match=["grpc", "protobuf", "protocol buffers"], langs=["Go", "C++"], topics=["grpc", "protobuf"],
                            seeds=["grpc/grpc", "grpc/grpc-go", "protocolbuffers/protobuf", "bufbuild/buf"]),
    "Workflow engines": dict(match=["workflow engine", "workflow orchestration", "orchestration", "temporal",
                                    "job queue", "job queues", "background jobs", "durable execution"],
                             langs=["Go", "TypeScript", "Python"], topics=["workflow-engine", "orchestration"],
                             seeds=["temporalio/temporal", "n8n-io/n8n", "windmill-labs/windmill", "hatchet-dev/hatchet",
                                    "inngest/inngest"]),
    "Search": dict(match=["search engine", "search infrastructure", "elasticsearch", "opensearch", "lucene",
                          "information retrieval", "full-text search", "search relevance"],
                   langs=["Java", "Rust", "Go", "C++"], topics=["search-engine", "search", "information-retrieval"],
                   seeds=["elastic/elasticsearch", "meilisearch/meilisearch", "typesense/typesense", "quickwit-oss/tantivy",
                          "apache/lucene", "qdrant/qdrant"]),
    "Real-time & collaboration": dict(match=["real-time", "realtime", "real time", "websockets", "websocket", "crdt",
                                             "crdts", "collaborative editing", "multiplayer", "sync engine", "local-first"],
                                      langs=["TypeScript", "Rust", "Go"], topics=["realtime", "crdt", "websocket"],
                                      seeds=["yjs/yjs", "automerge/automerge", "socketio/socket.io", "livekit/livekit"]),
    "Video & WebRTC": dict(match=["webrtc", "video streaming", "live streaming", "ffmpeg", "codecs", "codec", "video",
                                  "audio/video", "media pipeline"],
                           langs=["C", "C++", "Go", "Rust"], topics=["webrtc", "video", "ffmpeg"],
                           seeds=["FFmpeg/FFmpeg", "pion/webrtc", "livekit/livekit", "obsproject/obs-studio"]),
    "Auth & identity": dict(match=["authentication", "authorization", "oauth", "oidc", "identity", "sso", "iam",
                                   "access control", "passkeys", "webauthn"],
                            langs=["Go", "TypeScript", "Java"], topics=["authentication", "oauth2", "identity"],
                            seeds=["keycloak/keycloak", "ory/kratos", "zitadel/zitadel", "better-auth/better-auth"]),
    "Messaging & chat": dict(match=["messaging", "chat", "matrix protocol", "xmpp", "push notifications"],
                             langs=["Go", "Rust", "TypeScript", "Python"], topics=["chat", "messaging", "matrix"],
                             seeds=["element-hq/synapse", "mattermost/mattermost", "RocketChat/Rocket.Chat",
                                    "signalapp/libsignal"]),
    "API platforms": dict(match=["api design", "openapi", "api gateway", "developer platform", "public api",
                                 "public apis", "sdk", "sdks"],
                          langs=["TypeScript", "Go", "Python"], topics=["api", "openapi", "sdk"],
                          seeds=["OpenAPITools/openapi-generator", "swagger-api/swagger-ui", "Kong/kong",
                                 "hoppscotch/hoppscotch"]),

    # infrastructure
    "Infrastructure / DevOps": dict(match=[r"re:(?<!data )(?<![a-z])infra(?:structure)?(?![a-z])", "devops", "platform engineering", "platform team",
                                           "ci/cd", "cicd", "continuous integration", "terraform", "infrastructure as code",
                                           "iac", "pulumi", "ansible", "deployment"],
                                    langs=["Go", "Python", "HCL"], topics=["devops", "infrastructure", "terraform", "ci-cd"],
                                    seeds=["hashicorp/terraform", "opentofu/opentofu", "pulumi/pulumi", "argoproj/argo-cd",
                                           "ansible/ansible"]),
    "Kubernetes": dict(match=["kubernetes", "k8s", "containers", "docker", "helm", "container orchestration", "eks", "gke"],
                       langs=["Go"], topics=["kubernetes", "docker", "containers", "helm"],
                       seeds=["kubernetes/kubernetes", "helm/helm", "containerd/containerd", "moby/moby", "k3s-io/k3s"]),
    "Cloud": dict(match=["aws", "gcp", "google cloud", "azure", "serverless", "lambda", "cloud", "cloud-native",
                         "cloud native"],
                  langs=["Go", "TypeScript", "Python"], topics=["aws", "cloud", "serverless", "cloud-native"],
                  seeds=["aws/aws-cdk", "serverless/serverless", "aws/aws-sdk-go-v2"]),
    "SRE & observability": dict(match=["sre", "site reliability", "observability", "monitoring",
                                       "tracing", "opentelemetry", "prometheus", "grafana", "on-call", "incident response",
                                       "logging", "metrics"],
                                langs=["Go", "Rust"], topics=["observability", "monitoring", "opentelemetry", "prometheus"],
                                seeds=["prometheus/prometheus", "grafana/grafana", "open-telemetry/opentelemetry-collector",
                                       "jaegertracing/jaeger", "vectordotdev/vector"]),
    "Networking": dict(match=["networking", "network protocols", "tcp/ip", "tcp", "load balancer", "load balancing",
                              "proxy", "proxies", "cdn", "dns", "service mesh", "http/2", "quic"],
                       langs=["Go", "Rust", "C++", "C"], topics=["networking", "proxy", "load-balancer"],
                       seeds=["envoyproxy/envoy", "cloudflare/pingora", "istio/istio", "caddyserver/caddy",
                              "traefik/traefik"]),
    "Build systems": dict(match=["bazel", "build systems", "build system", "monorepo", "monorepos", "nix", "nixos"],
                          langs=["Go", "Java", "Rust", "Nix"], topics=["build-system", "bazel", "monorepo", "nix"],
                          seeds=["bazelbuild/bazel", "vercel/turborepo", "nrwl/nx", "NixOS/nix"]),
    "Developer tools": dict(match=["developer tools", "developer tooling", "devtools", "dev tools", "developer experience",
                                   "dx", "cli", "command-line", "ide", "ides", "editor", "language server", "lsp",
                                   "linters", "compilers and tooling"],
                            langs=["Rust", "TypeScript", "Go"], topics=["developer-tools", "devtools", "cli"],
                            seeds=["microsoft/vscode", "neovim/neovim", "astral-sh/uv", "astral-sh/ruff", "biomejs/biome",
                                   "zed-industries/zed"]),
    "Testing & QA": dict(match=["test automation", "qa", "quality assurance", "testing", "end-to-end tests", "e2e",
                                "playwright", "cypress", "selenium", "test infrastructure"],
                         langs=["TypeScript", "Python", "Java"], topics=["testing", "test-automation", "e2e-testing"],
                         seeds=["microsoft/playwright", "cypress-io/cypress", "vitest-dev/vitest", "pytest-dev/pytest"]),

    # low-level
    "Systems programming": dict(match=["systems programming", "systems engineer", "low-level", "low level",
                                       r"re:(?<![a-z])(?:high[- ])?performance(?! computing)(?! review)", "memory management",
                                       "concurrency", "multithreading", "lock-free", "simd", "latency-sensitive"],
                                langs=["C++", "Rust", "C", "Zig"], topics=["systems-programming", "performance", "simd"],
                                seeds=["rust-lang/rust", "ziglang/zig", "tokio-rs/tokio", "facebook/folly",
                                       "abseil/abseil-cpp"]),
    "Compilers": dict(match=["compiler", "compilers", "llvm", "mlir", "parsers", "type systems", "type system",
                             "interpreters", "interpreter", "jit", "virtual machine", "programming languages",
                             "language design", "static analysis"],
                      langs=["C++", "Rust", "OCaml", "Haskell"], topics=["compiler", "llvm", "programming-language"],
                      seeds=["llvm/llvm-project", "rust-lang/rust", "swiftlang/swift", "microsoft/TypeScript",
                             "golang/go", "ziglang/zig"]),
    "Operating systems": dict(match=["linux kernel", "kernel", "operating systems", "operating system", "device drivers",
                                     "drivers", "hypervisor", "virtualization", "unix internals"],
                              langs=["C", "Rust", "C++"], topics=["kernel", "linux", "operating-system", "virtualization"],
                              seeds=["torvalds/linux", "firecracker-microvm/firecracker", "cloud-hypervisor/cloud-hypervisor",
                                     "qemu/qemu"]),
    "eBPF": dict(match=["ebpf", "bpf", "xdp"], langs=["C", "Go", "Rust"], topics=["ebpf", "bpf"],
                 seeds=["cilium/cilium", "iovisor/bcc", "aya-rs/aya", "cilium/ebpf", "falcosecurity/falco"]),
    "Embedded & firmware": dict(match=["embedded", "firmware", "microcontroller", "microcontrollers", "rtos", "iot",
                                       "hardware", "esp32", "arm cortex", "bare metal", "bare-metal", "fpga", "pcb"],
                                langs=["C", "C++", "Rust"], topics=["embedded", "firmware", "iot", "rtos", "esp32"],
                                seeds=["zephyrproject-rtos/zephyr", "espressif/esp-idf", "embassy-rs/embassy",
                                       "FreeRTOS/FreeRTOS-Kernel", "platformio/platformio-core"]),
    "Robotics": dict(match=["robotics", "robot", "robots", "ros", "ros2", "slam", "motion planning", "perception",
                            "control systems", "drones", "drone", "autonomy"],
                     langs=["C++", "Python"], topics=["robotics", "ros", "ros2", "slam"],
                     seeds=["ros2/rclcpp", "ros-navigation/navigation2", "PX4/PX4-Autopilot", "ArduPilot/ardupilot",
                            "google-deepmind/mujoco"]),
    "Autonomous vehicles": dict(match=["self-driving", "autonomous vehicles", "autonomous driving", "adas", "lidar"],
                                langs=["C++", "Python"], topics=["self-driving-car", "autonomous-driving", "lidar"],
                                seeds=["commaai/openpilot", "ApolloAuto/apollo", "carla-simulator/carla",
                                       "autowarefoundation/autoware"]),
    "Game development": dict(match=["game engine", "game engines", "game development", "gamedev", "games", "unity",
                                    "unreal", "unreal engine", "gameplay"],
                             langs=["C++", "C#", "Rust"], topics=["game-engine", "gamedev", "unity", "unreal-engine"],
                             seeds=["godotengine/godot", "bevyengine/bevy", "raysan5/raylib", "ocornut/imgui"]),
    "Graphics & GPU": dict(match=["graphics", "rendering", "renderer", "opengl", "vulkan", "metal", "shaders", "shader",
                                  "directx", "ray tracing"],
                           langs=["C++", "Rust"], topics=["graphics", "rendering", "vulkan", "opengl"],
                           seeds=["gfx-rs/wgpu", "google/filament", "bkaradzic/bgfx", "mrdoob/three.js"]),
    "AR / VR": dict(match=["augmented reality", "virtual reality", "mixed reality", "xr", "visionos", "vision pro",
                           "arkit", "arcore", "openxr", "spatial computing"],
                    langs=["Swift", "C#", "C++"], topics=["augmented-reality", "virtual-reality", "xr", "visionos"],
                    seeds=["KhronosGroup/OpenXR-SDK-Source", "mrdoob/three.js"]),
    "GPU programming": dict(match=["cuda", "gpu programming", "gpu kernels", "kernels", "hpc", "high performance computing",
                                   "triton", "rocm", "gpus", "gpu"],
                            langs=["CUDA", "C++", "Python"], topics=["cuda", "gpu", "hpc"],
                            seeds=["NVIDIA/cutlass", "triton-lang/triton", "pytorch/pytorch", "NVIDIA/cccl"]),

    # data & AI
    "Data engineering": dict(match=["data engineering", "data engineer", "data pipelines", "data pipeline", "etl", "elt",
                                    "airflow", "dbt", "data warehouse", "data warehousing", "lakehouse", "data lake",
                                    "snowflake", "bigquery", "data infrastructure", "data platform"],
                             langs=["Python", "Scala", "Java"], topics=["data-engineering", "etl", "airflow", "data-pipeline"],
                             seeds=["apache/airflow", "dbt-labs/dbt", "dagster-io/dagster", "PrefectHQ/prefect",
                                    "apache/iceberg", "delta-io/delta"]),
    "Big data": dict(match=["spark", "pyspark", "hadoop", "big data", "trino", "presto", "arrow", "parquet"],
                     langs=["Scala", "Java", "Python", "Rust"], topics=["spark", "big-data", "apache-arrow"],
                     seeds=["apache/spark", "apache/arrow", "trinodb/trino", "pola-rs/polars"]),
    "Data science": dict(match=["data science", "data scientist", "statistics", "statistical", "analytics", "pandas",
                                "a/b testing", "experimentation", "causal inference", "forecasting"],
                         langs=["Python", "Jupyter Notebook", "R"], topics=["data-science", "pandas", "statistics"],
                         seeds=["pandas-dev/pandas", "pola-rs/polars", "scikit-learn/scikit-learn", "numpy/numpy",
                                "statsmodels/statsmodels"]),
    "Machine learning": dict(match=["machine learning", "ml", "deep learning", "neural networks", "neural network",
                                    "pytorch", "tensorflow", "ml engineer", "ai/ml", "ml/ai", "modeling", "models",
                                    "scikit-learn", "xgboost", "prediction", "predictions", "predictive",
                                    "prediction engine", "prediction engines", "classifier", "classification"],
                             langs=["Python", "C++", "Jupyter Notebook"],
                             topics=["machine-learning", "deep-learning", "pytorch"],
                             seeds=["pytorch/pytorch", "scikit-learn/scikit-learn", "huggingface/transformers",
                                    "jax-ml/jax", "keras-team/keras"]),
    "LLMs & generative AI": dict(match=["llm", "llms", "large language model", "large language models", "generative ai",
                                        "genai", "gen ai", "gpt", "prompt engineering", "foundation models",
                                        "language models", "fine-tuning", "fine tuning", "transformers", "openai api",
                                        "ai products", "ai-native", "ai native", "ai"],
                                 langs=["Python", "TypeScript"], topics=["llm", "large-language-models", "generative-ai"],
                                 seeds=["huggingface/transformers", "vllm-project/vllm", "langchain-ai/langchain",
                                        "run-llama/llama_index", "ggml-org/llama.cpp", "huggingface/trl"]),
    "LLM inference": dict(match=["inference", "model serving", "serving", "quantization", "kv cache", "inference engine",
                                 "inference optimization", "latency optimization", "speculative decoding"],
                          langs=["Python", "C++", "CUDA", "Rust"], topics=["llm-inference", "inference", "model-serving"],
                          seeds=["vllm-project/vllm", "sgl-project/sglang", "NVIDIA/TensorRT-LLM", "ggml-org/llama.cpp",
                                 "ollama/ollama"]),
    "AI agents": dict(match=["agents", "agent", "agentic", "ai agents", "tool use", "tool calling", "multi-agent",
                             "mcp", "model context protocol", "copilot", "assistants"],
                      langs=["Python", "TypeScript"], topics=["ai-agents", "agents", "llm-agents"],
                      seeds=["langchain-ai/langgraph", "microsoft/autogen", "crewAIInc/crewAI",
                             "modelcontextprotocol/python-sdk", "openai/openai-agents-python", "browser-use/browser-use"]),
    "RAG & vector search": dict(match=["rag", "retrieval augmented", "retrieval-augmented", "vector database",
                                       "vector databases", "vector search", "embeddings", "semantic search", "retrieval"],
                                langs=["Python", "Rust", "Go"], topics=["rag", "vector-database", "embeddings"],
                                seeds=["run-llama/llama_index", "qdrant/qdrant", "weaviate/weaviate", "milvus-io/milvus",
                                       "chroma-core/chroma", "pgvector/pgvector"]),
    "Recommender systems": dict(match=["recommendation", "recommendations", "recommender", "recommender systems",
                                       "recsys", "ranking", "personalization", "personalisation", "feed ranking",
                                       "matching", "ads ranking"],
                                langs=["Python", "C++", "Scala"],
                                topics=["recommender-system", "recommendation-system", "recsys", "ranking"],
                                seeds=["meta-pytorch/torchrec", "tensorflow/recommenders", "recommenders-team/recommenders",
                                       "NVIDIA-Merlin/Merlin", "benfred/implicit"]),
    "Computer vision": dict(match=["computer vision", "image processing", "object detection", "segmentation", "opencv",
                                   "image recognition", "ocr", "vision models", "video understanding"],
                            langs=["Python", "C++"], topics=["computer-vision", "object-detection", "image-segmentation"],
                            seeds=["opencv/opencv", "ultralytics/ultralytics", "facebookresearch/detectron2",
                                   "facebookresearch/sam2", "roboflow/supervision"]),
    "NLP": dict(match=["nlp", "natural language processing", "text classification", "named entity", "tokenization",
                       "language understanding"],
                langs=["Python"], topics=["nlp", "natural-language-processing"],
                seeds=["huggingface/transformers", "explosion/spaCy", "huggingface/tokenizers", "nltk/nltk"]),
    "Speech & audio": dict(match=["speech", "speech recognition", "asr", "tts", "text-to-speech", "voice", "audio",
                                  "voice ai", "speech-to-text"],
                           langs=["Python", "C++"], topics=["speech-recognition", "text-to-speech", "audio"],
                           seeds=["openai/whisper", "ggml-org/whisper.cpp", "k2-fsa/sherpa-onnx", "SYSTRAN/faster-whisper",
                                  "pipecat-ai/pipecat"]),
    "Diffusion & image generation": dict(match=["diffusion", "diffusion models", "stable diffusion", "image generation",
                                                "video generation", "generative models", "text-to-image"],
                                         langs=["Python"], topics=["diffusion-models", "stable-diffusion", "image-generation"],
                                         seeds=["huggingface/diffusers", "Comfy-Org/ComfyUI",
                                                "AUTOMATIC1111/stable-diffusion-webui", "Stability-AI/generative-models"]),
    "Reinforcement learning": dict(match=["reinforcement learning", "rl", "rlhf", "reward modeling", "policy optimization"],
                                   langs=["Python"], topics=["reinforcement-learning", "rlhf"],
                                   seeds=["huggingface/trl", "Farama-Foundation/Gymnasium", "DLR-RM/stable-baselines3",
                                          "vwxyzjn/cleanrl", "verl-project/verl"]),
    "MLOps": dict(match=["mlops", "ml infrastructure", "ml infra", "ml platform", "feature store", "model deployment",
                         "ml pipelines", "experiment tracking", "ml systems"],
                  langs=["Python", "Go"], topics=["mlops", "machine-learning", "model-deployment"],
                  seeds=["mlflow/mlflow", "ray-project/ray", "kubeflow/kubeflow", "bentoml/BentoML", "feast-dev/feast"]),
    "Distributed training": dict(match=["distributed training", "large-scale training", "pretraining", "pre-training",
                                        "training infrastructure", "model parallelism", "gpu clusters", "training runs"],
                                 langs=["Python", "C++", "CUDA"], topics=["distributed-training", "deep-learning"],
                                 seeds=["deepspeedai/DeepSpeed", "NVIDIA/Megatron-LM", "pytorch/torchtitan",
                                        "huggingface/accelerate", "Lightning-AI/pytorch-lightning"]),
    "JAX": dict(match=["jax", "flax", "xla"], langs=["Python"], topics=["jax", "flax"],
                seeds=["jax-ml/jax", "google/flax", "google-deepmind/optax"]),
    "LLM evaluation": dict(match=["evals", "evaluation", "llm evaluation", "benchmarks", "benchmarking", "red teaming"],
                           langs=["Python", "TypeScript"], topics=["llm-evaluation", "evaluation", "benchmark"],
                           seeds=["openai/evals", "EleutherAI/lm-evaluation-harness", "confident-ai/deepeval",
                                  "promptfoo/promptfoo"]),

    # finance & crypto
    "Payments": dict(match=["payments", "payment", "payment processing", "billing", "ledger", "ledgers", "stripe",
                            "apple pay", "checkout", "card issuing", "money movement", "reconciliation", "invoicing"],
                     langs=["Go", "Java", "Ruby", "TypeScript"], topics=["payments", "stripe", "billing", "fintech"],
                     seeds=["stripe/stripe-ios", "stripe/stripe-node", "tigerbeetle/tigerbeetle", "juspay/hyperswitch",
                            "killbill/killbill", "getlago/lago"]),
    "Fintech": dict(match=["fintech", "banking", "neobank", "open banking", "plaid", "lending", "credit card",
                           "credit cards", "wallet", "wallets", "brokerage", "personal finance", "financial services",
                           "kyc", "aml", "compliance", "rewards"],
                    langs=["Go", "Python", "Java", "Kotlin", "Swift"], topics=["fintech", "banking", "finance", "open-banking"],
                    seeds=["actualbudget/actual", "firefly-iii/firefly-iii", "tigerbeetle/tigerbeetle"]),
    "Trading & quant": dict(match=["trading", "algorithmic trading", "hft", "high-frequency", "quant", "quantitative",
                                   "market data", "order book", "market making", "low latency", "low-latency", "exchange"],
                            langs=["C++", "Python", "Rust"], topics=["trading", "algorithmic-trading", "quantitative-finance"],
                            seeds=["nautechsystems/nautilus_trader", "QuantConnect/Lean", "microsoft/qlib",
                                   "freqtrade/freqtrade"]),
    "Web3": dict(match=["web3", "blockchain", "crypto", "cryptocurrency", "ethereum", "defi", "evm", "onchain",
                        "on-chain", "l2", "layer 2", "rollup", "rollups", "dapps"],
                 langs=["Rust", "Go", "TypeScript", "Solidity"], topics=["ethereum", "web3", "blockchain", "defi"],
                 seeds=["ethereum/go-ethereum", "paradigmxyz/reth", "alloy-rs/alloy", "wevm/viem", "OffchainLabs/nitro"]),
    "Smart contracts": dict(match=["smart contracts", "smart contract", "solidity", "foundry", "hardhat", "vyper",
                                   "auditing", "audits", "erc-20", "erc20"],
                            langs=["Solidity", "Vyper"], topics=["smart-contracts", "solidity", "foundry"],
                            seeds=["OpenZeppelin/openzeppelin-contracts", "foundry-rs/foundry", "Uniswap/v4-core",
                                   "Vectorized/solady"]),
    "Zero-knowledge": dict(match=["zero-knowledge", "zero knowledge", "zk", "zkp", "zk-snarks", "snarks", "starks",
                                  "zkvm", "circuits", "plonk"],
                           langs=["Rust", "C++"], topics=["zero-knowledge", "zk", "zkp", "snark"],
                           seeds=["succinctlabs/sp1", "risc0/risc0", "iden3/circom", "zcash/halo2", "arkworks-rs/algebra"]),
    "Solana": dict(match=["solana", "anchor framework", "svm"], langs=["Rust", "TypeScript"], topics=["solana", "anchor"],
                   seeds=["anza-xyz/agave", "otter-sec/anchor", "jito-foundation/jito-solana"]),
    "Bitcoin & Lightning": dict(match=["bitcoin", "lightning network", "lightning", "btc"], langs=["C++", "Rust", "Go"],
                                topics=["bitcoin", "lightning-network"],
                                seeds=["bitcoin/bitcoin", "lightningnetwork/lnd", "rust-bitcoin/rust-bitcoin",
                                       "lightningdevkit/rust-lightning"]),

    # security
    "Security": dict(match=["security", "appsec", "application security", "penetration testing", "pentesting",
                            "vulnerability", "vulnerabilities", "threat modeling", "secure coding", "infosec",
                            "cybersecurity", "fuzzing", "reverse engineering", "detection engineering"],
                     langs=["Go", "Python", "Rust", "C"], topics=["security", "cybersecurity", "fuzzing"],
                     seeds=["projectdiscovery/nuclei", "aquasecurity/trivy", "google/oss-fuzz", "falcosecurity/falco",
                            "AFLplusplus/AFLplusplus"]),
    "Cryptography": dict(match=["cryptography", "cryptographic", "encryption", "end-to-end encryption", "e2ee", "tls",
                                "pki", "key management", "hsm", "mpc", "threshold signatures"],
                         langs=["Rust", "C", "Go"], topics=["cryptography", "encryption", "tls"],
                         seeds=["openssl/openssl", "briansmith/ring", "jedisct1/libsodium", "FiloSottile/age",
                                "signalapp/libsignal"]),
    "Privacy": dict(match=["privacy", "privacy-preserving", "differential privacy", "gdpr", "data protection"],
                    langs=["Rust", "Python"], topics=["privacy"], seeds=[]),

    # domains
    "E-commerce": dict(match=["e-commerce", "ecommerce", "shopify", "marketplace", "marketplaces", "retail",
                              "storefront"],
                       langs=["TypeScript", "Ruby", "PHP"], topics=["ecommerce", "e-commerce", "shopify"],
                       seeds=["medusajs/medusa", "saleor/saleor", "Shopify/hydrogen", "vendurehq/vendure"]),
    "Healthcare": dict(match=["healthcare", "health tech", "healthtech", "hipaa", "fhir", "ehr", "clinical", "medical",
                              "biotech", "bioinformatics", "genomics"],
                       langs=["Python", "TypeScript", "Java"], topics=["healthcare", "fhir", "bioinformatics", "medical"],
                       seeds=["hapifhir/hapi-fhir", "medplum/medplum", "Project-MONAI/MONAI", "openemr/openemr"]),
    "Geospatial": dict(match=["geospatial", "gis", "mapping", "maps", "gps", "routing", "location data", "openstreetmap"],
                       langs=["C++", "Python", "TypeScript"], topics=["gis", "geospatial", "maps", "openstreetmap"],
                       seeds=["maplibre/maplibre-gl-js", "Project-OSRM/osrm-backend", "valhalla/valhalla", "uber/h3",
                              "OSGeo/gdal"]),
    "Web scraping": dict(match=["web scraping", "scraping", "crawling", "crawler", "crawlers", "browser automation"],
                         langs=["Python", "TypeScript"], topics=["web-scraping", "crawler", "scraping"],
                         seeds=["scrapy/scrapy", "apify/crawlee", "microsoft/playwright", "firecrawl/firecrawl"]),
    "Quantum computing": dict(match=["quantum", "quantum computing", "qubit", "qubits", "quantum error correction",
                                     "qiskit", "cirq"],
                              langs=["Python", "C++", "Rust", "Julia"], topics=["quantum-computing", "quantum", "qiskit"],
                              seeds=["Qiskit/qiskit", "quantumlib/Cirq", "PennyLaneAI/pennylane", "quantumlib/Stim",
                                     "NVIDIA/cuda-quantum"]),
    "SaaS & startups": dict(match=["startup", "start-up", "startups", "saas", "b2b", "early-stage", "early stage",
                                   "0 to 1", "0-to-1", "zero to one", "0→1", "seed-stage", "series a", "founding team",
                                   "product-minded", "product minded", "mvp"],
                            langs=["TypeScript", "Python", "Go"], topics=["saas", "startup", "open-source"],
                            seeds=["PostHog/posthog", "supabase/supabase", "twentyhq/twenty", "documenso/documenso",
                                   "formbricks/formbricks"]),
}

# Languages a JD names directly. Case-sensitive patterns avoid "go to market", "Series C" or "React to feedback".
LANGUAGES = {
    "Python": r"(?i:python|pytorch|django|fastapi)",
    "Go": r"\bGo\b(?![- ]to\b)|(?i:\bgolang\b)",
    "Rust": r"(?i:\brust\b)",
    "TypeScript": r"(?i:\btypescript\b|\bts\b)",
    "JavaScript": r"(?i:\bjavascript\b|\bjs\b|\bnode\.?js\b)",
    "Java": r"(?i:\bjava\b)(?!\s*script)",
    "Kotlin": r"(?i:\bkotlin\b)",
    "Swift": r"\bSwift(?:UI)?\b",
    "Objective-C": r"(?i:objective-c|\bobjc\b)",
    "C++": r"(?i:c\+\+|\bcpp\b)",
    "C": r"\bC/C\+\+|\bC (?:and|or) C\+\+|\b(?:in|ANSI|embedded) C\b(?![+#])|\bC programming\b",
    "C#": r"(?i:c#|\.net\b|\bdotnet\b)",
    "Ruby": r"(?i:\bruby\b)",
    "Elixir": r"(?i:\belixir\b)",
    "Erlang": r"(?i:\berlang\b)",
    "Scala": r"(?i:\bscala\b)",
    "Haskell": r"(?i:\bhaskell\b)",
    "OCaml": r"(?i:\bocaml\b)",
    "Clojure": r"(?i:\bclojure\b)",
    "Zig": r"(?i:\bzig\b)",
    "PHP": r"(?i:\bphp\b|\blaravel\b)",
    "Dart": r"\bDart\b|(?i:\bflutter\b)",
    "Lua": r"(?i:\blua\b)",
    "Solidity": r"(?i:\bsolidity\b)",
    "Vyper": r"(?i:\bvyper\b)",
    "CUDA": r"(?i:\bcuda\b)",
    "Julia": r"(?i:\bjulia(?:lang| language| programming)\b)",
    "Mojo": r"\bMojo\b",
    "Nix": r"(?i:\bnix(?:os)?\b)",
}

COMPANIES = {
    "google": r"google|deepmind", "meta": r"\bmeta\b|facebook", "apple": r"\bapple\b", "amazon": r"amazon",
    "microsoft": r"microsoft", "netflix": r"netflix", "stripe": r"\bstripe\b", "airbnb": r"airbnb", "uber": r"\buber\b",
    "robinhood": r"robinhood", "coinbase": r"coinbase", "plaid": r"\bplaid\b", "ramp": r"\bramp\b", "brex": r"\bbrex\b",
    "square": r"\bsquare\b|cash app|\bblock inc", "openai": r"openai", "anthropic": r"anthropic", "nvidia": r"nvidia",
    "databricks": r"databricks", "snowflake": r"snowflake", "figma": r"\bfigma\b", "notion": r"\bnotion\b",
    "vercel": r"vercel", "shopify": r"shopify", "palantir": r"palantir", "jane street": r"jane street",
    "citadel": r"citadel", "two sigma": r"two sigma", "hudson river trading": r"hudson river|\bhrt\b", "tesla": r"tesla",
    "spacex": r"spacex", "linkedin": r"linkedin", "twitter": r"twitter|\bx\.com\b", "cloudflare": r"cloudflare",
    "datadog": r"datadog", "mistral": r"mistral", "xai": r"\bxai\b", "y combinator": r"y combinator|\byc\b",
}
SCHOOLS = {
    "stanford": r"stanford", "mit": r"\bmit\b", "harvard": r"harvard", "berkeley": r"berkeley|\bucb\b",
    "cmu": r"\bcmu\b|carnegie mellon", "princeton": r"princeton", "caltech": r"caltech", "oxford": r"oxford",
    "cambridge": r"cambridge", "eth zurich": r"\beth\b|eth zurich", "waterloo": r"waterloo", "iit": r"\biit\b",
    "tsinghua": r"tsinghua", "toronto": r"university of toronto|\buoft\b",
}

LOCATIONS = [
    ("sf", r"san francisco|bay area|silicon valley|\bsf\b|palo alto|mountain view|menlo park|sunnyvale|south bay"),
    ("nyc", r"new york|\bnyc\b|manhattan|brooklyn"),
    ("london", r"\blondon\b"), ("uk", r"united kingdom|\buk\b"),
    ("germany", r"germany|berlin|munich|münchen|hamburg"), ("europe", r"\beurope\b|\bemea\b|\beu\b"),
    ("india", r"\bindia\b|bangalore|bengaluru|hyderabad|\bpune\b"), ("singapore", r"singapore"),
    ("dubai", r"dubai|\buae\b|abu dhabi"), ("seattle", r"seattle"), ("austin", r"\baustin\b"), ("boston", r"boston"),
    ("los angeles", r"los angeles"), ("chicago", r"chicago"), ("toronto", r"toronto"), ("paris", r"\bparis\b"),
    ("amsterdam", r"amsterdam"), ("zurich", r"zurich|zürich"), ("tel aviv", r"tel aviv"), ("denver", r"\bdenver\b"),
    ("usa", r"united states|\busa\b|\bus-based\b|\bu\.s\.(?:a\.)?"),
]

# Section headings, checked in order; the weight is how much a skill mentioned under that heading counts.
SECTIONS = [
    ("skip", 0, r"benefits|perks|what we offer|we offer|compensation|salary|pay range|equity and|equal (?:employment )?"
                r"opportunit|\beeo\b|how to apply|application process|interview process|our process|hiring process|"
                r"reject|red flags|not a (?:good )?fit|who should not|disqualif|deal ?breakers|we will not|don'?t apply|"
                r"logistics|location and|\bvisa\b|not required|not needed|no need|don'?t need|not looking for|"
                r"not expected|not necessary"),
    ("nice", 1, r"nice[- ]to[- ]haves?|bonus|preferred|pluses|\ba plus\b|good to have|extra credit|would be great|"
                r"ideally|desirable|stand out|great if|signals|not filters"),
    ("about", 0.5, r"about (?:us|the company|the team|[A-Z][\w.]+$)|who we are|our (?:mission|story|team|company)|"
                   r"company (?:overview|facts|background)|project overview|overview|the company|why (?:us|join)|"
                   r"backed by|traction|working environment|culture|our values|founders?|team size|funding"),
    ("must", 3, r"requirements?|required|must[- ]haves?|qualifications?|what you(?:'ll| will)? (?:need|bring)|"
                r"what we(?:'re| are) looking for|who (?:you are|we(?:'re| are) looking for)|about you|you (?:have|are|bring)|"
                r"ideal candidate|skills|experience|the (?:person|guy|candidate)|candidate profile|looking for|"
                r"mandatory|essential"),
    ("do", 2, r"responsibilit|what you(?:'ll| will) (?:do|build|own|work on)|the role|about the role|role overview|"
              r"in this role|day[- ]to[- ]day|your impact|you will|the job|scope|what you'll be doing|the work|"
              r"what (?:they|you)(?:'ll| will) (?:build|do|own)|outcomes|first \d+(?:[–-]\d+)? (?:days|months)|"
              r"success looks like|30/60/90|projects you"),
]
NICE_CUE = re.compile(r"\b(bonus|nice to have|nice-to-have|a plus|is a plus|preferred|ideally|optional|great if|not required)\b", re.I)
MUST_CUE = re.compile(r"\b(must|required|requires|strong|deep|expert|expertise|proven|extensive|exceptional|solid|"
                      r"world-class|significant|mastery|fluent|fluency|should have|you have|you've|you know|"
                      r"know .{0,30} inside out|proficien\w*|hands-on)\b", re.I)
NEGATIVE = re.compile(r"\b(not looking for|don'?t need|do not need|no need for|isn'?t required|we don'?t want|avoid|"
                      r"won'?t consider|will not consider|not a fit|reject)\b", re.I)
ROLE_NOUN = re.compile(r"\b(engineers?|developers?|scientists?|architects?|researchers?|cto|programmers?|sre|devops|"
                       r"hacker|builder|technical lead|tech lead)\b", re.I)
LEVELS = [("intern", r"\bintern(ship)?\b"), ("junior", r"\bjunior\b|\bentry[- ]level\b|\bnew grad"),
          ("senior", r"\bsenior\b|\bsr\.?\b"), ("staff", r"\bstaff\b"), ("principal", r"\bprincipal\b"),
          ("lead", r"\blead\b|\btech lead\b"), ("founding", r"\bfounding\b|\bfirst engineer|\bengineer #?1\b"),
          ("head", r"\bhead of\b|\bvp\b|\bdirector\b|\bcto\b")]
WEIGHT_SIGNALS = [
    ("impact", 10, r"\bshipped\b|\bship\b|ownership|\bown\b|end[- ]to[- ]end|0 to 1|0-to-1|zero to one|from scratch|"
                   r"side projects?|built (?:and|&) launched|real users|products? (?:people|users)|founding|startup"),
    ("seed", 10, r"open[- ]source|maintainer|contributor|contributions to|core contributor|upstream"),
    ("notable", 5, r"research|papers?|publications?|phd|neurips|icml|iclr|state of the art|sota"),
    ("activity", 5, r"fast[- ]paced|velocity|move fast|ships? (?:fast|quickly|daily|weekly)|hands[- ]on|prolific"),
]
TITLE_LABEL = re.compile(r"^(?:role|title|position|job title|job|opening|hiring for)\s*[:\-–—]\s*(.+)$", re.I)
JUNK_REPO = re.compile(r"awesome|roadmap|tutorial|interview|course|\bbooks?\b|learn|cheat-?sheet|examples?|resources|"
                       r"\blist\b|guide|curated|papers|notes|collection|beginners|100-days|leetcode|system-design|"
                       r"public-apis|projects|template|boilerplate|starter|-docs$|^docs$|\.github\.io", re.I)


# Writing Python or Go says nothing about someone's domain; writing Swift, Solidity or CUDA does.
DISTINCTIVE_LANGUAGES = {"Swift", "Objective-C", "Kotlin", "Dart", "Solidity", "Vyper", "CUDA", "Elixir", "Erlang",
                         "Scala", "Haskell", "OCaml", "Zig", "Julia", "Mojo", "Svelte", "Vue", "Nix", "HCL"}
# Words too common in bios and repo descriptions to count as evidence of a skill.
GENERIC_TERMS = {"api", "apis", "services", "web services", "models", "modeling", "ai", "node", "cli", "ide", "ides",
                 "editor", "storage", "video", "chat", "agent", "agents", "matching", "evaluation", "benchmarks",
                 "testing", "qa", "e2e", "dx", "cloud", "lambda", "metrics", "logging", "monitoring", "tracing",
                 "proxy", "proxies", "dns", "tcp", "cdn", "graphics", "rendering", "metal", "kernels", "gpu", "gpus",
                 "maps", "mapping", "routing", "gps", "games", "robot", "robots", "drone", "drones", "speech", "voice",
                 "audio", "retrieval", "ranking", "serving", "inference", "identity", "sso", "iam", "rewards", "wallet",
                 "wallets", "exchange", "quant", "lightning", "btc", "retail", "messaging", "realtime", "real time",
                 "real-time", "performance", "concurrency", "kernel", "drivers", "compliance", "privacy", "spark",
                 "arrow", "rails", "phoenix", "beam", "otp", "bun", "deno", "expo", "hyper", "prediction",
                 "predictions", "classification", "classifier", "recommendation", "recommendations", "personalization",
                 "personalisation", "infrastructure", "infra", "deployment", "sdk", "sdks", "public api", "public apis",
                 "web app", "web apps", "browser", "polish", "animation", "animations", "figma", "hardware", "iot",
                 "ml", "rl", "zk", "l2", "mvp", "b2b", "startup", "startups", "crypto", "mcp", "copilot", "assistants",
                 "search engine", "security", "vulnerability", "auditing", "audits", "encryption", "tls", "lending",
                 "trading", "consensus", "scalability", "at scale", "large scale", "large-scale", "replication",
                 "database", "databases", "caching", "streaming", "orchestration", "temporal", "kmp", "ror", "ts",
                 "embeddings", "evals", "tts", "asr", "ocr", "segmentation", "diffusion", "perception", "autonomy",
                 "unity", "unreal", "shader", "shaders", "ledger", "ledgers", "checkout", "billing", "medical",
                 "clinical", "mobile app", "mobile apps", "consumer app", "consumer apps", "native apps", "app store"}


def _alias_pattern(alias):
    if alias.startswith("re:"):
        return re.compile(alias[3:], re.I)
    if alias.startswith("="):
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias[1:])}(?![A-Za-z0-9])")
    return re.compile(rf"(?<![a-z0-9]){re.escape(alias.lower())}(?![a-z0-9])", re.I)


SKILL_PATTERNS = {name: [_alias_pattern(name)] + [_alias_pattern(a) for a in spec["match"]]
                  for name, spec in SKILLS.items()}
LANGUAGE_PATTERNS = {name: re.compile(pattern) for name, pattern in LANGUAGES.items()}


# -- reading the JD -----------------------------------------------------------

def _clean(line):
    return re.sub(r"^[\s#>*•·▪◦‣–—\-]+|[\s*_:]+$", "", line).strip()


def _heading_kind(raw):
    """(kind, weight) if this line looks like a section heading, else None."""
    text = _clean(raw)
    if not text or len(text) > 70 or len(text.split()) > 9:
        return None
    # Bulleted lines are content; numbered ones are often headings ("5. Expected outcomes"), if short.
    bullet = re.match(r"^\s*[-*•·▪◦‣]\s", raw) or (re.match(r"^\s*\d+[.)]\s", raw) and text.endswith("."))
    heading_like = (raw.rstrip().endswith(":") or raw.lstrip().startswith("#") or raw.strip().startswith("**")
                    or text.isupper() or not text.endswith((".", ",", ";")))
    if bullet and not raw.rstrip().endswith(":"):
        return None
    if not heading_like:
        return None
    for kind, weight, pattern in SECTIONS:
        if re.search(pattern, text, re.I):
            return kind, weight
    return None


def weighted_lines(text):
    """[(line, weight, kind)] where weight says how much a mention on that line should count."""
    raws = [raw for raw in text.splitlines() if raw.strip()]
    # Text before the first heading is usually the title and company intro. A JD with no headings at all is a
    # paragraph or two about the role itself, so it counts like a responsibilities section.
    structured = any((_heading_kind(raw) or ("skip",))[0] in ("must", "do", "nice") for raw in raws)
    kind, weight = ("default", 1.0) if structured else ("do", 2.0)
    out = []
    for raw in raws:
        if not raw.strip():
            continue
        heading = _heading_kind(raw)
        if heading:
            kind, weight = heading
            # A heading like "Experience with Swift and Go:" carries content too.
            out.append((_clean(raw), max(weight, 0) if kind != "skip" else 0, kind))
            continue
        line, w, line_kind = _clean(raw), weight, kind
        if kind != "skip":
            if NEGATIVE.search(line):
                w = 0
            elif NICE_CUE.search(line) and kind != "about":
                w, line_kind = min(w, 1), "nice"  # "a plus", "bonus if…" inside any section
            elif MUST_CUE.search(line) and kind in ("default", "do", "must"):
                w = w * 1.3
        out.append((line, w, line_kind))
    return out


def find_title(text):
    lines = [_clean(l) for l in text.splitlines() if _clean(l)][:20]
    for line in lines:
        match = TITLE_LABEL.match(line)
        if match:
            return _tidy_title(match.group(1))
    for line in lines:
        if ROLE_NOUN.search(line) and len(line.split()) <= 14 and not line.endswith("."):
            return _tidy_title(line)
    return ""


def _tidy_title(title):
    title = re.sub(r"\s*\([^)]*\b(?:remote|hybrid|on-?site|in[- ]person|full[- ]time|part[- ]time|contract|f/m|m/f)\b[^)]*\)",
                   "", title, flags=re.I)
    title = re.sub(r"^(?:we(?:'re| are) hiring:?\s*(?:an?\s+)?|hiring:?\s*(?:an?\s+)?|join us as\s+(?:an?\s+)?)", "",
                   title, flags=re.I)
    # Keep the part that names the role; the rest is usually the company, location or job type.
    parts = [p.strip() for p in re.split(r"\s+[|–—]\s+|\s+-\s+|\s+@\s+|\s+at\s+(?=[A-Z])", title) if p.strip()]
    title = next((p for p in parts if ROLE_NOUN.search(p)), parts[0] if parts else title)
    title = re.sub(r"\s*\((?:remote|hybrid|on-?site|full[- ]time|contract|[^)]*\b(?:sf|nyc|london)\b)[^)]*\)\s*$", "",
                   title, flags=re.I)
    return title.strip(" ,.-*_")[:80]


def find_seniority(text):
    levels = [name for name, pattern in LEVELS if re.search(pattern, text, re.I)]
    years = [int(n) for n in re.findall(r"(\d{1,2})\s*\+?\s*(?:\+\s*)?(?:years|yrs)", text, re.I) if 0 < int(n) <= 25]
    return levels, max(years) if years else None


def find_location(lines):
    # Benefits and similar sections often list offices ("offices in SF and London") that aren't the job's location.
    # The company blurb ("offices in Berlin") only counts when the role itself names no place.
    role_lines = [line for line, w, kind in lines if kind not in ("skip", "about")
                  or re.search(r"^location|in[- ]person|on[- ]?site|hybrid", line, re.I)]
    about_lines = [line for line, w, kind in lines if kind == "about"]
    found = []
    for chunk in (role_lines, about_lines):
        place = "\n".join(chunk)
        low = place.lower()
        found = [key for key, pattern in LOCATIONS if re.search(pattern, low)]
        if found:
            break
    place = "\n".join(role_lines + about_lines)
    low = place.lower()
    if "usa" not in found and re.search(r"\bUS\b", place):  # capital "US" is the country, not "us"
        found.append("usa")
    if "london" in found and "uk" in found:
        found.remove("uk")
    if "sf" in found and "usa" in found:
        found.remove("usa")
    remote = bool(re.search(r"\bremote\b", low)) and not re.search(r"\b(?:not|no|non)[- ]remote\b|\bremote (?:is )?not\b", low)
    onsite = bool(re.search(r"in[- ]person|on[- ]?site|in[- ]office|in the office|\bhybrid\b|relocat", low))
    everything = "\n".join(line for line, w, kind in lines).lower()
    visa = bool(re.search(r"visa sponsorship|sponsor(?:s|ing)? (?:your |work )?visas?|will sponsor|visa support|"
                          r"h-?1b|relocation support", everything)) and not re.search(
        r"(?:no|not|unable to|cannot|can't|won't|don't|do not) (?:offer |provide )?(?:visa )?sponsor|"
        r"sponsorship (?:is )?(?:not|un)(?:available| available|offered| offered|possible| possible)|"
        r"without (?:visa )?sponsorship", everything)
    return found, remote, onsite, visa


def _hits(pattern_or_list, text):
    patterns = pattern_or_list if isinstance(pattern_or_list, list) else [pattern_or_list]
    return any(p.search(text) for p in patterns)


def analyze(text):
    """Read a JD and return {"role": roles.json-style role, "analysis": what was understood, for people to check}."""
    text = (text or "").replace("\r\n", "\n").replace("\u00a0", " ")
    if len(text.strip()) < 40:
        raise ValueError("Paste the full job description (at least a few lines with the requirements).")
    lines = weighted_lines(text)
    counted = [(line, w, kind) for line, w, kind in lines if w > 0]

    skills = {}
    for line, w, kind in counted:
        for name, patterns in SKILL_PATTERNS.items():
            if _hits(patterns, line):
                s = skills.setdefault(name, {"score": 0.0, "must": False, "mentions": 0, "kinds": set()})
                s["score"] += w
                s["mentions"] += 1
                s["kinds"].add(kind)
                # Named under requirements or responsibilities, or with "strong", "must", "deep"… on the line.
                if kind in ("must", "do") or (w >= 2.5 and kind not in ("nice", "about")):
                    s["must"] = True
    # A skill named only in passing (once, in the company blurb) isn't a requirement.
    skills = {k: v for k, v in skills.items() if v["score"] >= 1}
    ranked = sorted(skills, key=lambda k: (skills[k]["must"], skills[k]["score"]), reverse=True)

    languages = {}
    for line, w, kind in counted:
        for name, pattern in LANGUAGE_PATTERNS.items():
            if pattern.search(line):
                languages[name] = languages.get(name, 0) + w
    if not ranked and not languages:
        raise ValueError("Couldn't find any technical skills or languages in that text. "
                         "Paste the whole JD, including the requirements.")

    body = "\n".join(line for line, w, kind in lines if kind != "skip")
    title = find_title(text)
    # Seniority from the title and the role's own sections, not from bios of founders in the company blurb.
    levels, years = find_seniority("\n".join([title] + [line for line, w, kind in lines if kind in ("must", "do", "nice")]))
    if not years:
        years = find_seniority(body)[1]
    locations, remote, onsite, visa = find_location(lines)
    # Companies and schools as candidate signals only from the role's sections, not the company's own blurb
    # (where they're customers, investors or the founders' backgrounds).
    wanted = "\n".join(line for line, w, kind in lines if kind in ("must", "nice", "do", "default"))
    companies = [name for name, pattern in COMPANIES.items() if re.search(pattern, wanted, re.I)]
    schools = [name for name, pattern in SCHOOLS.items() if re.search(pattern, wanted, re.I)]

    top = ranked[:12]
    lang_rank = {}
    for name, score in languages.items():
        lang_rank[name] = score * 2  # named outright: stronger than implied by a skill
    for i, name in enumerate(top):
        for j, lang in enumerate(SKILLS[name]["langs"]):
            lang_rank[lang] = lang_rank.get(lang, 0) + skills[name]["score"] / (1 + i * 0.3) / (1 + j)
    role_languages = sorted(lang_rank, key=lang_rank.get, reverse=True)[:10]
    searchable = [l for l in role_languages if l not in ("CSS", "HCL", "Nix", "Jupyter Notebook", "Vue", "Svelte")]
    explicit = sorted(languages, key=languages.get, reverse=True)
    named = [l for l in explicit if l in searchable or l in LANGUAGES]
    # Languages the JD names outright beat ones implied by a skill; two or more named is enough to search on.
    search_languages = named[:3] if len(named) >= 2 else list(dict.fromkeys(named + searchable))[:3]

    topics = list(dict.fromkeys(t for name in top for t in SKILLS[name]["topics"]))[:30]
    topic_search = list(dict.fromkeys(SKILLS[name]["topics"][0] for name in top[:3]))
    keywords = list(dict.fromkeys(
        [name.lower() for name in top if _plain_name(name)]
        + [a for name in top[:8] for a in _terms(name)[:4]]
        + [lvl for lvl in levels if lvl in ("founding", "staff", "principal")]
        + (["startup", "yc", "y combinator"] if "SaaS & startups" in skills or "y combinator" in companies else [])
        + companies + schools))[:45]

    seed_why = {}
    for name in top:
        for repo in SKILLS[name]["seeds"][:4]:
            seed_why.setdefault(repo, name)
    seeds = round_robin({name: [r for r in SKILLS[name]["seeds"][:4] if seed_why[r] == name] for name in top},
                        [[n for n in top if skills[n]["must"]], [n for n in top if not skills[n]["must"]]])

    weights = dict(DEFAULT_WEIGHTS)
    weights["fit"] += 5  # the JD's requirements feed into fit
    for part, bump, pattern in WEIGHT_SIGNALS:
        if re.search(pattern, body, re.I):
            weights[part] += bump
    if {"founding", "lead", "head"} & set(levels):
        weights["impact"] += 5
    if {"senior", "staff", "principal"} & set(levels) or (years or 0) >= 5:
        weights["experience"] += 5
    if "junior" in levels or "intern" in levels:
        weights["experience"] = max(0, weights["experience"] - 5)
    total = sum(weights.values())
    weights = {k: round(v * 100 / total) for k, v in weights.items()}

    min_followers = 5 if {"junior", "intern"} & set(levels) else 20
    if {"staff", "principal", "founding", "head"} & set(levels) or (years or 0) >= 7:
        min_followers = 30

    title = title or (top[0] + " Engineer" if top else "Custom role")
    requirements = [{
        "name": name, "must": skills[name]["must"],
        # Only a skill's own main language is evidence: Swift for iOS, Solidity for smart contracts.
        "languages": SKILLS[name]["langs"][:1] if SKILLS[name]["langs"][0] in DISTINCTIVE_LANGUAGES else [],
        "topics": SKILLS[name]["topics"],
        "keywords": list(dict.fromkeys(([name.lower()] if _plain_name(name) else []) + _terms(name))),
        "repos": SKILLS[name]["seeds"],
    } for name in top[:10]]
    # The JD's main language is a must-have; others named are usually alternatives ("Go, or Java/Kotlin").
    for i, lang in enumerate(explicit[:4]):
        if not any(r["name"] == lang for r in requirements):
            requirements.append({"name": lang, "must": i == 0 and languages[lang] >= 2.5, "languages": [lang],
                                 "topics": [], "keywords": [], "repos": []})

    if remote and not onsite and not locations:
        location = ""
    else:
        location = ", ".join(locations[:4])
    notes = []
    if remote and not onsite:
        notes.append("Remote role: searching everywhere unless you set a location." if not locations else
                     "Remote role with a location named; clear the location to search everywhere.")
    if onsite and locations:
        notes.append(f"In-person or hybrid in {', '.join(locations[:3])}.")
    if visa:
        notes.append("They sponsor visas, so strong people outside the office location are worth a look too: "
                     "try a second search without a location.")
    if years:
        notes.append(f"Asks for {years}+ years of experience.")
    if not ranked:
        notes.append("Only languages were recognised; add topics or seed repos to sharpen the search.")

    role = {
        "label": title, "languages": role_languages or search_languages, "search_languages": search_languages,
        "topics": topics, "topic_search": topic_search, "keywords": keywords, "seed_repos": seeds,
        "weights": weights, "min_followers": min_followers, "requirements": requirements,
    }
    analysis = {
        "title": title, "skills": [{"name": n, "must": skills[n]["must"], "score": round(skills[n]["score"], 1)}
                                   for n in ranked],
        "languages": explicit, "levels": levels, "years": years, "location": location, "remote": remote,
        "onsite": onsite, "visa": visa, "companies": companies, "schools": schools, "notes": notes,
        "seed_info": [{"repo": r, "why": seed_why[r], "source": "dictionary"} for r in seeds],
    }
    return {"role": role, "analysis": analysis, "key": slug(title)}


def round_robin(by_skill, groups, limit=15):
    """Seed repos one per skill in rank order, so top skills come first and every skill gets some.

    `groups` is [must-have skills, nice-to-have skills]: nice-to-haves only fill what the must-haves leave."""
    seeds = []
    for order in groups:
        depth = 0
        while len(seeds) < limit and any(len(by_skill.get(s, [])) > depth for s in order):
            for skill in order:
                repos = by_skill.get(skill, [])
                if len(repos) > depth and repos[depth] not in seeds and len(seeds) < limit:
                    seeds.append(repos[depth])
            depth += 1
    return seeds


def _plain_name(skill):
    """Skill names like "Kafka" work as keywords; "SRE & observability" doesn't."""
    low = skill.lower()
    return bool(re.fullmatch(r"[a-z0-9 .+#-]{2,24}", low)) and low not in GENERIC_TERMS


def _terms(skill):
    """A skill's phrases that are specific enough to count as evidence in a bio or repo description."""
    return [a.lower() for a in SKILLS[skill]["match"]
            if a[:1] != "=" and not a.startswith("re:") and len(a) > 2 and a.lower() not in GENERIC_TERMS]


def slug(title):
    words = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return ("jd-" + words)[:40].rstrip("-") or "jd-role"


# -- GitHub: check seed repos and find more -------------------------------------

REPO_FIELDS = "nameWithOwner stargazerCount pushedAt isArchived description primaryLanguage { name }"
REPO_SEARCH = """
query($q: String!) {
  search(query: $q, type: REPOSITORY, first: 12) { nodes { ... on Repository { %s } } }
}""" % REPO_FIELDS


def _check_repos(gh, repos):
    """{requested name: repo data or None if it doesn't exist}; {} if GitHub couldn't be asked."""
    found = {}
    for start in range(0, len(repos), 30):
        chunk = repos[start:start + 30]
        parts = []
        for i, repo in enumerate(chunk):
            owner, name = repo.split("/", 1)
            parts.append(f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {REPO_FIELDS} }}")
        try:
            data = gh.graphql("query {\n" + "\n".join(parts) + "\n}")
        except GitHubError as err:
            log(f"  ! could not check seed repos: {err}")
            return {}  # unknown: keep them all
        for i, repo in enumerate(chunk):
            found[repo] = data.get(f"r{i}")
    return found


def refine_with_github(gh, result, max_seeds=15, now=None):
    """Check the seed repos on GitHub and add popular, active repos for the top skills.

    Repos that are gone, archived or untouched for a year are dropped, and repos written in the role's languages are
    preferred, so a Go backend role gets Go payment projects rather than an iOS SDK.
    """
    now = now or datetime.now(timezone.utc)
    role, analysis = result["role"], result["analysis"]
    skills = [s for s in analysis["skills"] if s["name"] in SKILLS][:12]
    langs = {l.lower() for l in role["languages"][:6]}
    owner = {}
    for skill in skills:
        for repo in SKILLS[skill["name"]]["seeds"]:
            owner.setdefault(repo, skill["name"])
    checked = _check_repos(gh, list(owner))
    stale = (now - timedelta(days=365)).isoformat()

    def usable(data):
        return data and not data.get("isArchived") and (data.get("pushedAt") or "") >= stale

    def in_role_language(data):
        return ((data or {}).get("primaryLanguage") or {}).get("name", "").lower() in langs

    by_skill, info = {}, {}
    for skill in skills:
        name = skill["name"]
        repos = [r for r in SKILLS[name]["seeds"] if owner[r] == name and (r not in checked or usable(checked[r]))]
        repos.sort(key=lambda r: not in_role_language(checked.get(r)))  # stable: keeps the curated order otherwise
        for repo in repos[:4]:
            data = checked.get(repo) or {}
            full = data.get("nameWithOwner") or repo
            info[full] = {"repo": full, "why": name, "source": "dictionary", "stars": data.get("stargazerCount")}
            by_skill.setdefault(name, []).append(full)

    recent = (now - timedelta(days=180)).strftime("%Y-%m-%d")
    for skill in skills[:6]:
        name = skill["name"]
        topic = SKILLS[name]["topics"][0]
        # Broad topics ("backend", "mobile") mix every stack, so search them in the role's own language.
        lang = next((l for l in role["search_languages"] if l in SKILLS[name]["langs"]), None)
        queries = [f"topic:{topic} language:{json.dumps(lang)}"] if lang else []
        queries.append(f"topic:{topic}")
        nodes = []
        for query in queries:
            try:
                nodes = [n for n in gh.graphql(REPO_SEARCH, {
                    "q": f"{query} stars:>=1000 pushed:>={recent} archived:false fork:false sort:stars-desc"})
                    ["search"]["nodes"] if n]
            except (GitHubError, KeyError, TypeError) as err:
                log(f"  ! repo search for {topic}: {err}")
            if len(nodes) >= 3:
                break
        nodes.sort(key=lambda n: not in_role_language(n))
        picks = 0
        for repo in nodes:
            full = repo.get("nameWithOwner")
            if (not full or full in info or JUNK_REPO.search(full)
                    or JUNK_REPO.search((repo.get("description") or "")[:80])):
                continue
            info[full] = {"repo": full, "why": name, "source": "github", "stars": repo.get("stargazerCount")}
            by_skill.setdefault(name, []).append(full)
            picks += 1
            if picks == 2:
                break

    seeds = round_robin(by_skill, [[s["name"] for s in skills if s["must"]], [s["name"] for s in skills if not s["must"]]],
                        max_seeds)
    if seeds:
        role["seed_repos"] = seeds
    analysis["seed_info"] = [info.get(r) or {"repo": r, "why": "", "source": "dictionary"} for r in role["seed_repos"]]
    return result
