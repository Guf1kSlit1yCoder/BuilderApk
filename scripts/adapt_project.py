import os
import sys
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


def setup_native_wrapper_project(project_dir: Path) -> bool:
    """Оборачивает чистые C++/CMake/NDK проекты (без Gradle) в полноценный Android Gradle проект."""
    existing_gradle = [
        f for f in project_dir.rglob("*.gradle*")
        if "build" not in f.parts and f.name in ("build.gradle", "build.gradle.kts")
    ]
    if existing_gradle:
        return False

    print(f"  [*] Detected pure C++/NDK project without Gradle in: {project_dir}")

    # 1. Поиск CMakeLists.txt или Android.mk
    cmake_file = None
    if (project_dir / "CMakeLists.txt").exists():
        cmake_file = project_dir / "CMakeLists.txt"
    elif (project_dir / "jni" / "CMakeLists.txt").exists():
        cmake_file = project_dir / "jni" / "CMakeLists.txt"

    mk_file = None
    if (project_dir / "jni" / "Android.mk").exists():
        mk_file = project_dir / "jni" / "Android.mk"
    elif (project_dir / "Android.mk").exists():
        mk_file = project_dir / "Android.mk"

    if not cmake_file and not mk_file:
        for cm in project_dir.rglob("CMakeLists.txt"):
            if "build" not in cm.parts:
                cmake_file = cm
                break
        if not cmake_file:
            for mk in project_dir.rglob("Android.mk"):
                if "build" not in mk.parts:
                    mk_file = mk
                    break

    # 2. Определение имени нативной библиотеки и поддерживаемых архитектур (ABI)
    lib_name = "native_lib"
    abi_list = ["arm64-v8a"]

    if cmake_file and cmake_file.exists():
        txt = cmake_file.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"add_library\s*\(\s*([a-zA-Z0-9_-]+)\s+(SHARED|STATIC)", txt, re.IGNORECASE)
        if m:
            lib_name = m.group(1)
        elif (project_dir / "jni" / "CMakeLists.txt").exists():
            txt_jni = (project_dir / "jni" / "CMakeLists.txt").read_text(encoding="utf-8", errors="ignore")
            m_jni = re.search(r"add_library\s*\(\s*([a-zA-Z0-9_-]+)\s+(SHARED|STATIC)", txt_jni, re.IGNORECASE)
            if m_jni:
                lib_name = m_jni.group(1)

    if mk_file and mk_file.exists():
        txt_mk = mk_file.read_text(encoding="utf-8", errors="ignore")
        m_mk = re.search(r"LOCAL_MODULE\s*:=\s*([a-zA-Z0-9_-]+)", txt_mk)
        if m_mk:
            lib_name = m_mk.group(1)

    app_mk = (project_dir / "jni" / "Application.mk") if (project_dir / "jni" / "Application.mk").exists() else (project_dir / "Application.mk")
    if app_mk.exists():
        txt_app = app_mk.read_text(encoding="utf-8", errors="ignore")
        m_app = re.search(r"APP_ABI\s*:=\s*(.+)", txt_app)
        if m_app:
            parsed_abis = [a.strip() for a in m_app.group(1).split() if a.strip() and a.strip() != "all"]
            if parsed_abis:
                abi_list = parsed_abis

    clean_lib = re.sub(r"[^a-zA-Z0-9]", "", lib_name).lower() or "app"
    app_id = f"com.nativeapp.{clean_lib}"
    app_name = lib_name.replace("_", " ").title()

    # 3. Корневой settings.gradle
    settings_file = project_dir / "settings.gradle"
    settings_file.write_text(
        "pluginManagement {\n"
        "    repositories {\n"
        "        google()\n"
        "        mavenCentral()\n"
        "        gradlePluginPortal()\n"
        "    }\n"
        "}\n"
        "dependencyResolutionManagement {\n"
        "    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)\n"
        "    repositories {\n"
        "        google()\n"
        "        mavenCentral()\n"
        "    }\n"
        "}\n"
        f"rootProject.name = '{clean_lib}_app'\n"
        "include ':app'\n",
        encoding="utf-8",
    )

    # 4. Корневой build.gradle
    root_gradle = project_dir / "build.gradle"
    root_gradle.write_text(
        "buildscript {\n"
        "    repositories {\n"
        "        google()\n"
        "        mavenCentral()\n"
        "    }\n"
        "    dependencies {\n"
        "        classpath 'com.android.tools.build:gradle:8.5.2'\n"
        "    }\n"
        "}\n"
        "allprojects {\n"
        "    repositories {\n"
        "        google()\n"
        "        mavenCentral()\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    # 5. Модуль app и app/build.gradle
    app_dir = project_dir / "app"
    app_dir.mkdir(parents=True, exist_ok=True)

    abi_filters_str = ", ".join(f"'{a}'" for a in abi_list)

    native_build_block = ""
    if cmake_file:
        rel_cmake = os.path.relpath(cmake_file, app_dir).replace("\\", "/")
        native_build_block = f"""
    externalNativeBuild {{
        cmake {{
            path file('{rel_cmake}')
        }}
    }}
"""
    elif mk_file:
        rel_mk = os.path.relpath(mk_file, app_dir).replace("\\", "/")
        native_build_block = f"""
    externalNativeBuild {{
        ndkBuild {{
            path file('{rel_mk}')
        }}
    }}
"""

    app_build_gradle = app_dir / "build.gradle"
    app_build_gradle.write_text(
        f"""plugins {{
    id 'com.android.application'
}}

android {{
    namespace '{app_id}'
    compileSdk 34

    defaultConfig {{
        applicationId '{app_id}'
        minSdk 21
        targetSdk 34
        versionCode 1
        versionName '1.0'

        ndk {{
            abiFilters {abi_filters_str}
        }}
        externalNativeBuild {{
            cmake {{
                cppFlags '-std=c++17'
                arguments '-DANDROID_STL=c++_shared'
            }}
            ndkBuild {{
                arguments 'APP_STL=c++_shared'
            }}
        }}
    }}

    buildTypes {{
        release {{
            minifyEnabled false
            proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'
        }}
        debug {{
            debuggable true
        }}
    }}

    compileOptions {{
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }}
{native_build_block}
}}

dependencies {{
    implementation 'androidx.appcompat:appcompat:1.6.1'
}}
""",
        encoding="utf-8",
    )

    # 6. AndroidManifest.xml
    src_main = app_dir / "src" / "main"
    src_main.mkdir(parents=True, exist_ok=True)
    manifest_file = src_main / "AndroidManifest.xml"
    manifest_file.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{app_id}">

    <application
        android:allowBackup="true"
        android:label="{app_name}"
        android:supportsRtl="true"
        android:theme="@android:style/Theme.DeviceDefault.NoActionBar">
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
""",
        encoding="utf-8",
    )

    # 7. MainActivity.java
    java_dir = src_main / "java" / Path(*app_id.split("."))
    java_dir.mkdir(parents=True, exist_ok=True)
    main_activity = java_dir / "MainActivity.java"
    main_activity.write_text(
        f"""package {app_id};

import android.app.Activity;
import android.os.Bundle;
import android.util.Log;
import android.widget.TextView;

public class MainActivity extends Activity {{
    private static final String TAG = "{lib_name}";

    static {{
        try {{
            System.loadLibrary("{lib_name}");
            Log.i(TAG, "Native library '{lib_name}' loaded successfully!");
        }} catch (Throwable t) {{
            Log.w(TAG, "Native library '{lib_name}' could not be loaded: " + t.getMessage());
        }}
    }}

    @Override
    protected void onCreate(Bundle savedInstanceState) {{
        super.onCreate(savedInstanceState);
        TextView tv = new TextView(this);
        tv.setText("{app_name} is running.\\nNative library '{lib_name}' loaded.");
        tv.setTextSize(18);
        tv.setPadding(40, 60, 40, 40);
        setContentView(tv);
    }}
}}
""",
        encoding="utf-8",
    )

    # 8. gradle.properties
    gp = project_dir / "gradle.properties"
    gp.write_text(
        "android.useAndroidX=true\n"
        "android.enableJetifier=true\n"
        "org.gradle.parallel=true\n"
        "org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8\n",
        encoding="utf-8",
    )

    print(f"  [+] Generated Gradle Android wrapper for native project: {lib_name} (ABIs: {abi_list})")
    return True


def adapt_android_project(project_dir: Path) -> None:
    project_dir = project_dir.resolve()
    print(f"=== Adapting Android Project: {project_dir} ===")

    # 0. Проверяем наличие Gradle-проекта. Если чистый C++/NDK/CMake — генерируем обёртку.
    setup_native_wrapper_project(project_dir)

    # 1. settings.gradle
    settings_file = project_dir / "settings.gradle"
    settings_kts = project_dir / "settings.gradle.kts"
    app_dir = project_dir / "app"

    if not settings_file.exists() and not settings_kts.exists():
        if app_dir.exists() and any((app_dir / f).exists() for f in ("build.gradle", "build.gradle.kts")):
            settings_file.write_text(
                "pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }\n"
                "dependencyResolutionManagement { repositories { google(); mavenCentral() } }\n"
                "rootProject.name = 'App'\n"
                "include ':app'\n",
                encoding="utf-8",
            )
        else:
            settings_file.write_text("rootProject.name = 'App'\n", encoding="utf-8")
        print("  [+] Created settings.gradle")

    # 2. Modernize repositories in all gradle files
    for gf in project_dir.rglob("*.gradle*"):
        if "build" in gf.parts:
            continue
        try:
            txt = gf.read_text(encoding="utf-8", errors="ignore")
            new_txt = txt.replace("jcenter()", "mavenCentral()")
            new_txt = re.sub(r"com\.android\.tools\.build:gradle:[0-7]\.[0-9.]+", "com.android.tools.build:gradle:8.5.2", new_txt)
            if new_txt != txt:
                gf.write_text(new_txt, encoding="utf-8")
                print(f"  [+] Modernized repositories/AGP in {gf.relative_to(project_dir)}")
        except Exception:
            pass

    # 3. Detect target module
    target_dir = app_dir if app_dir.exists() and (app_dir / "build.gradle").exists() else project_dir
    target_gradle = target_dir / "build.gradle"
    if not target_gradle.exists():
        target_gradle = target_dir / "build.gradle.kts"

    # 4. Detect package name and namespace
    manifest_file = None
    for cand in [
        target_dir / "src" / "main" / "AndroidManifest.xml",
        project_dir / "src" / "main" / "AndroidManifest.xml",
    ]:
        if cand.exists():
            manifest_file = cand
            break
    if not manifest_file:
        for cand in project_dir.rglob("AndroidManifest.xml"):
            if "build" not in cand.parts:
                manifest_file = cand
                break

    pkg_name = None
    activities = []
    if manifest_file:
        try:
            tree = ET.parse(manifest_file)
            root = tree.getroot()
            pkg_name = root.attrib.get("package")
            for app in root.findall("application"):
                for act in app.findall("activity"):
                    name = act.attrib.get("{http://schemas.android.com/apk/res/android}name", "")
                    activities.append(name)
        except Exception:
            pass

    if target_gradle and target_gradle.exists():
        try:
            c = target_gradle.read_text(encoding="utf-8", errors="ignore")
            m_ns = re.search(r"namespace\s+['\"]([a-zA-Z0-9_.]+)['\"]", c)
            if m_ns:
                pkg_name = m_ns.group(1)
            elif not pkg_name:
                m_id = re.search(r"applicationId\s+['\"]([a-zA-Z0-9_.]+)['\"]", c)
                if m_id:
                    pkg_name = m_id.group(1)
        except Exception:
            pass

    if not pkg_name:
        pkg_name = "com.builder.app"

    print(f"  [+] Package/Namespace: {pkg_name}")

    # 5. Detect available NDK versions (prevents CXX1104 version mismatch)
    available_ndks = []
    ndk_search_paths = [
        Path("/usr/local/lib/android/sdk/ndk"),
        Path(os.environ.get("ANDROID_HOME", "")) / "ndk",
        Path(os.environ.get("ANDROID_NDK_HOME", "")),
        Path(os.environ.get("ANDROID_NDK", "")),
    ]
    for sp in ndk_search_paths:
        if sp and sp.is_dir():
            if (sp / "source.properties").exists():
                available_ndks.append(sp)
            else:
                for sub in sp.iterdir():
                    if sub.is_dir() and (sub / "source.properties").exists():
                        available_ndks.append(sub)

    installed_ndk_version = None
    if available_ndks:
        def ndk_priority(p: Path):
            v = p.name
            if v.startswith("27."):
                return (10, v)
            if v.startswith("26."):
                return (9, v)
            if v.startswith("25."):
                return (8, v)
            if v.startswith("29."):
                return (7, v)
            if v.startswith("28."):
                return (6, v)
            return (1, v)

        best_ndk = sorted(available_ndks, key=ndk_priority)[-1]
        installed_ndk_version = best_ndk.name
        print(f"  [+] Selected installed NDK: {installed_ndk_version} ({best_ndk})")

    # 6. Update target build.gradle
    if target_gradle and target_gradle.exists() and target_gradle.suffix == ".gradle":
        c = target_gradle.read_text(encoding="utf-8", errors="ignore")

        if "namespace" not in c:
            c = re.sub(r"(android\s*\{)", f'\\1\n    namespace "{pkg_name}"', c, count=1)
            print(f'  [+] Injected namespace "{pkg_name}"')

        if "compileSdkVersion" in c:
            c = re.sub(r"compileSdkVersion\s+[0-9]+", "compileSdkVersion 34", c)
        elif "compileSdk " in c:
            c = re.sub(r"compileSdk\s+[0-9]+", "compileSdk 34", c)
        else:
            c = re.sub(r"(android\s*\{)", "\\1\n    compileSdk 34", c, count=1)

        if "minSdkVersion" in c:
            c = re.sub(r"minSdkVersion\s+([0-9]+)", lambda m: f"minSdkVersion {max(21, int(m.group(1)))}", c)

        # Wire up Android.mk
        mk_candidates = [f for f in project_dir.rglob("Android.mk") if "build" not in f.parts]
        if mk_candidates and "ndkBuild" not in c and "externalNativeBuild" not in c:
            rel_mk = mk_candidates[0].relative_to(target_dir).as_posix()
            injection = f"""
    externalNativeBuild {{
        ndkBuild {{
            path file("{rel_mk}")
        }}
    }}
    defaultConfig {{
        ndk {{
            abiFilters "arm64-v8a"
        }}
    }}
"""
            c = re.sub(r"(android\s*\{)", "\\1" + injection, c, count=1)
            print(f"  [+] Injected ndkBuild ({rel_mk})")

        # Wire up CMakeLists.txt if Android.mk not present
        if not mk_candidates and "externalNativeBuild" not in c:
            cmake_cands = [f for f in project_dir.rglob("CMakeLists.txt") if "build" not in f.parts]
            if cmake_cands:
                rel_cmake = cmake_cands[0].relative_to(target_dir).as_posix()
                injection = f"""
    externalNativeBuild {{
        cmake {{
            path file("{rel_cmake}")
        }}
    }}
    defaultConfig {{
        ndk {{
            abiFilters "arm64-v8a"
        }}
    }}
"""
                c = re.sub(r"(android\s*\{)", "\\1" + injection, c, count=1)
                print(f"  [+] Injected cmake ({rel_cmake})")

        # Set compatible ndkVersion if native code is used
        has_native = bool(mk_candidates or [f for f in project_dir.rglob("CMakeLists.txt") if "build" not in f.parts])
        if has_native and installed_ndk_version:
            if "ndkVersion" in c:
                c = re.sub(r'ndkVersion\s+[\'"][^\'"]+[\'"]', f'ndkVersion "{installed_ndk_version}"', c)
            else:
                c = re.sub(r"(android\s*\{)", r'\g<1>' + f'\n    ndkVersion "{installed_ndk_version}"', c, count=1)
            print(f'  [+] Set android.ndkVersion "{installed_ndk_version}"')

        if "compileOptions" not in c:
            c = re.sub(
                r"(android\s*\{)",
                "\\1\n    compileOptions {\n        sourceCompatibility JavaVersion.VERSION_1_8\n        targetCompatibility JavaVersion.VERSION_1_8\n    }",
                c,
                count=1,
            )

        if "signingConfigs.debug" not in c and "buildTypes" in c:
            c = re.sub(
                r"buildTypes\s*\{(\s*release\s*\{)",
                r"buildTypes {\1\n            signingConfig signingConfigs.debug",
                c,
            )

        if "libs" not in c and "fileTree" not in c:
            c += '\ndependencies {\n    implementation fileTree(dir: "libs", include: ["*.jar", "*.aar"])\n}\n'

        target_gradle.write_text(c, encoding="utf-8")
        print(f"  [+] Updated {target_gradle.relative_to(project_dir)}")

    # 7. Package AIDE classes if exists
    for cand in [target_dir / "build" / "bin" / "classesdebug", target_dir / "build" / "bin" / "classesrelease"]:
        if cand.exists():
            libs_dir = target_dir / "libs"
            libs_dir.mkdir(parents=True, exist_ok=True)
            jar_path = libs_dir / "aide_classes.jar"
            with zipfile.ZipFile(jar_path, "w", compression=zipfile.ZIP_DEFLATED) as jz:
                for root, dirs, files in os.walk(cand):
                    for file in files:
                        if file.endswith(".class") and not file.startswith("R$") and file != "R.class":
                            fpath = Path(root) / file
                            rel = fpath.relative_to(cand)
                            jz.write(fpath, str(rel))
            shutil.rmtree(target_dir / "build", ignore_errors=True)
            print(f"  [+] Packaged AIDE classes into {jar_path.relative_to(project_dir)}")
            break

    # 8. Check Java/Kotlin sources
    base_src = app_dir if app_dir.exists() else project_dir
    src_main_java = base_src / "src" / "main" / "java"
    has_sources = False
    if src_main_java.exists():
        has_sources = any(f.suffix in (".java", ".kt") for f in src_main_java.rglob("*"))

    if not has_sources:
        pkg_parts = pkg_name.split(".")
        target_src_dir = src_main_java / Path(*pkg_parts)
        target_src_dir.mkdir(parents=True, exist_ok=True)
        act_name = "MainActivity"
        if activities:
            raw_act = activities[0].lstrip(".")
            if "." in raw_act:
                act_name = raw_act.split(".")[-1]
            else:
                act_name = raw_act
        main_java = target_src_dir / f"{act_name}.java"
        if not main_java.exists():
            main_java.write_text(
                f"""package {pkg_name};

import android.app.Activity;
import android.os.Bundle;
import android.widget.TextView;

public class {act_name} extends Activity {{
    @Override
    protected void onCreate(Bundle savedInstanceState) {{
        super.onCreate(savedInstanceState);
        TextView tv = new TextView(this);
        tv.setText("App loaded successfully");
        setContentView(tv);
    }}
}}
""",
                encoding="utf-8",
            )
            print(f"  [+] Created fallback activity: {main_java.relative_to(project_dir)}")

    # 9. Bump Application.mk
    for app_mk in project_dir.rglob("Application.mk"):
        mk_text = app_mk.read_text(encoding="utf-8", errors="ignore")
        mk_text = re.sub(r"APP_PLATFORM\s*:=\s*android-(1[0-9]|20)", "APP_PLATFORM := android-21", mk_text)
        app_mk.write_text(mk_text, encoding="utf-8")

    # 10. AIDL Path Normalization
    for f in project_dir.rglob("*.aidl"):
        if "aidl" in f.parts:
            c = f.read_text(errors="ignore")
            m = re.search(r"package\s+([a-zA-Z0-9_.]+)", c)
            if m:
                expected_pkg_path = m.group(1).replace(".", "/")
                aidl_root = Path(*f.parts[:f.parts.index("aidl")+1])
                target_file = aidl_root / expected_pkg_path / f.name
                if f.resolve() != target_file.resolve():
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(f), str(target_file))

    # 11. Clean ndk.dir from local.properties (causes CXX1104 error)
    for lp in project_dir.rglob("local.properties"):
        try:
            lines = [l for l in lp.read_text(encoding="utf-8", errors="ignore").splitlines() if not l.strip().startswith("ndk.dir")]
            lp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"  [+] Removed deprecated ndk.dir from {lp.relative_to(project_dir)}")
        except Exception:
            pass

    # 12. gradle.properties
    gp = project_dir / "gradle.properties"
    gp_text = gp.read_text(encoding="utf-8", errors="ignore") if gp.exists() else ""
    gp_text = gp_text.replace("android.useAndroidX=false", "android.useAndroidX=true")
    props = {
        "android.useAndroidX": "true",
        "android.enableJetifier": "true",
        "android.suppressUnsupportedCompileSdk": "37.2",
        "android.nonTransitiveRClass": "false",
        "org.gradle.parallel": "true",
    }
    for k, v in props.items():
        if k not in gp_text:
            gp_text += f"\n{k}={v}"
    if "org.gradle.jvmargs" not in gp_text:
        gp_text += "\norg.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8"
    gp.write_text(gp_text.strip() + "\n", encoding="utf-8")
    print("  [+] Optimized gradle.properties")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    adapt_android_project(target)
