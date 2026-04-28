# -*- coding: utf-8 -*-
import os

def generate_launcher():
    main_py_path = os.path.join('..', 'main.py')
    with open(main_py_path, 'rb') as f:
        main_content = f.read()
    
    hex_array = ', '.join(f'0x{b:02x}' for b in main_content)
    
    c_code = '''#include <windows.h>
#include <stdio.h>
#include <string.h>

unsigned char EMBEDDED_MAIN_PY[] = {''' + hex_array + '''};
unsigned int EMBEDDED_MAIN_PY_LEN = sizeof(EMBEDDED_MAIN_PY);

int main(int argc, char* argv[]) {
    char exePath[MAX_PATH];
    char pythonPath[MAX_PATH];
    char tempPyPath[MAX_PATH];
    char cmdLine[MAX_PATH * 4];
    int result = 1;
    
    GetModuleFileName(NULL, exePath, MAX_PATH);
    char* lastSlash = strrchr(exePath, '\\\\');
    if (lastSlash) *(lastSlash + 1) = '\\0';
    
    snprintf(pythonPath, MAX_PATH, "%sruntime\\\\python.exe", exePath);
    if (GetFileAttributes(pythonPath) == INVALID_FILE_ATTRIBUTES) {
        strcpy(pythonPath, "python.exe");
    }
    
    snprintf(tempPyPath, MAX_PATH, "%s__main__.py", exePath);
    DeleteFile(tempPyPath);
    
    FILE* f = fopen(tempPyPath, "wb");
    if (!f) {
        MessageBox(NULL, "Failed to create temporary file!", "Error", MB_OK | MB_ICONERROR);
        return 1;
    }
    
    fwrite(EMBEDDED_MAIN_PY, 1, EMBEDDED_MAIN_PY_LEN, f);
    fclose(f);
    
    snprintf(cmdLine, sizeof(cmdLine), "\\"%s\\" \\"%s\\"", pythonPath, tempPyPath);
    
    STARTUPINFO si = { sizeof(si) };
    PROCESS_INFORMATION pi;
    
    if (CreateProcess(NULL, cmdLine, NULL, NULL, FALSE, 0, NULL, exePath, &si, &pi)) {
        WaitForSingleObject(pi.hProcess, INFINITE);
        DWORD exitCode;
        GetExitCodeProcess(pi.hProcess, &exitCode);
        result = (int)exitCode;
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
    } else {
        MessageBox(NULL, "Failed to start Python.", "Error", MB_OK | MB_ICONERROR);
        result = 1;
    }
    
    DeleteFile(tempPyPath);
    return result;
}
'''
    
    with open('launcher.c', 'w', encoding='utf-8') as f:
        f.write(c_code)
    
    print(f"Generated launcher.c ({len(main_content)} bytes)")

def compile_exe():
    icon_path = os.path.join('..', 'app_icon.ico')
    if os.path.exists(icon_path):
        icon_rc = 'id ICON "../app_icon.ico"'
        with open('icon.rc', 'w') as f:
            f.write(icon_rc)
        os.system('windres --input icon.rc --output icon.o 2>nul || echo 0 > icon.o')
        os.system('gcc -O2 -s -o CelliumAgent.exe launcher.c icon.o -mwindows')
        print("Compiled with icon")
    else:
        os.system('gcc -O2 -s -o CelliumAgent.exe launcher.c')
        print("Compiled without icon")

if __name__ == "__main__":
    generate_launcher()
    compile_exe()
    print("Done: CelliumAgent.exe")
