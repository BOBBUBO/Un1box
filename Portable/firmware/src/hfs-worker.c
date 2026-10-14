#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <hfs/hfsplus.h>
#include <hfs/hfslib.h>
#include "abstractfile.h"
char endianness;
static int copy_attrs(Volume *src, Volume *dst, HFSCatalogNodeID from, HFSCatalogNodeID to) {
    BTree *tree = src->attrTree;
    if (!tree)
        return 1;
    uint32_t node = tree->headerRec->firstLeafNode, visited = 0;
    while (node) {
        if (node >= tree->headerRec->totalNodes || ++visited > tree->headerRec->totalNodes)
            return 0;
        BTNodeDescriptor *desc = readBTNodeDescriptor(node, tree);
        if (!desc || desc->kind != kBTLeafNode) {
            free(desc);
            return 0;
        }
        for (int i = 0; i < desc->numRecords; i++) {
            HFSPlusAttrKey *key = (HFSPlusAttrKey *)READ_KEY(tree, getRecordOffset(i, node, tree), tree->io);
            if (!key) {
                free(desc);
                return 0;
            }
            if (key->fileID == from) {
                for (int j = 0; j < key->name.length; j++)
                    if (key->name.unicode[j] > 127) {
                        free(key);
                        free(desc);
                        return 0;
                    }
                char *name = unicodeToAscii(&key->name);
                uint8_t *data = NULL;
                size_t size = getAttribute(src, from, name, &data);
                int ok = data && size <= 1024 * 1024 && setAttribute(dst, to, name, data, size);
                free(name);
                free(data);
                if (!ok) {
                    free(key);
                    free(desc);
                    return 0;
                }
            }
            free(key);
        }
        node = desc->fLink;
        free(desc);
    }
    return 1;
}
static int copy_tree(Volume *src, Volume *dst, const char *from, const char *to, int depth) {
    if (depth > 32 || strlen(from) > 700 || strlen(to) > 700)
        return 0;
    HFSPlusCatalogRecord *s = getRecordFromPath3(from, src, NULL, NULL, TRUE, FALSE, kHFSRootFolderID), *d = NULL;
    if (!s)
        return 0;
    int ok = 0;
    if (s->recordType == kHFSPlusFolderRecord) {
        d = getRecordFromPath(to, dst, NULL, NULL);
        if (!d) {
            if (!newFolder(to, dst))
                goto done;
            d = getRecordFromPath(to, dst, NULL, NULL);
        }
        if (!d || d->recordType != kHFSPlusFolderRecord)
            goto done;
        HFSPlusCatalogFolder *sf = (HFSPlusCatalogFolder *)s, *df = (HFSPlusCatalogFolder *)d;
        df->flags = sf->flags;
        df->permissions = sf->permissions;
        df->createDate = sf->createDate;
        df->contentModDate = sf->contentModDate;
        df->attributeModDate = sf->attributeModDate;
        df->accessDate = sf->accessDate;
        df->backupDate = sf->backupDate;
        df->userInfo = sf->userInfo;
        df->finderInfo = sf->finderInfo;
        if (!updateCatalog(dst, d) || !copy_attrs(src, dst, sf->folderID, df->folderID))
            goto done;
        CatalogRecordList *head = getFolderContents(sf->folderID, src), *it = head;
        ok = 1;
        for (; it; it = it->next) {
            if (it->record->recordType != kHFSPlusFolderRecord && it->record->recordType != kHFSPlusFileRecord)
                continue;
            for (int i = 0; i < it->name.length; i++)
                if (it->name.unicode[i] > 127) {
                    ok = 0;
                    break;
                }
            if (!ok)
                break;
            char *name = unicodeToAscii(&it->name), a[1024], b[1024];
            if (!name || !*name || strchr(name, '/') || !strcmp(name, ".") || !strcmp(name, "..")) {
                free(name);
                ok = 0;
                break;
            }
            snprintf(a, sizeof(a), "%s%s%s", from, strcmp(from, "/") ? "/" : "", name);
            snprintf(b, sizeof(b), "%s%s%s", to, strcmp(to, "/") ? "/" : "", name);
            free(name);
            if (!copy_tree(src, dst, a, b, depth + 1)) {
                ok = 0;
                break;
            }
        }
        releaseCatalogRecordList(head);
    } else if (s->recordType == kHFSPlusFileRecord) {
        HFSPlusCatalogFile *sf = (HFSPlusCatalogFile *)s;
        /* Reject unsupported forks/attributes rather than silently drop them. */
        if (sf->resourceFork.logicalSize || (sf->permissions.ownerFlags & UF_COMPRESSED) ||
            sf->userInfo.fileType == kHardLinkFileType || sf->dataFork.logicalSize > 128 * 1024 * 1024)
            goto done;
        void *buffer = malloc(1);
        size_t size = 0;
        if (!buffer)
            goto done;
        AbstractFile *memory = createAbstractFileFromMemoryFile(&buffer, &size);
        writeToFile(sf, memory, src);
        memory->seek(memory, 0);
        int copied = add_hfs(dst, memory, to);
        free(buffer);
        if (!copied)
            goto done;
        d = getRecordFromPath(to, dst, NULL, NULL);
        if (!d || d->recordType != kHFSPlusFileRecord)
            goto done;
        HFSPlusCatalogFile *df = (HFSPlusCatalogFile *)d;
        df->flags = sf->flags;
        df->permissions = sf->permissions;
        df->createDate = sf->createDate;
        df->contentModDate = sf->contentModDate;
        df->attributeModDate = sf->attributeModDate;
        df->accessDate = sf->accessDate;
        df->backupDate = sf->backupDate;
        df->userInfo = sf->userInfo;
        df->finderInfo = sf->finderInfo;
        ok = updateCatalog(dst, d) && copy_attrs(src, dst, sf->fileID, df->fileID);
    }
done:
    free(s);
    free(d);
    return ok;
}
int lt_fw_hfs_command(int argc, char **argv) {
    if (argc < 4)
        return 2;
    short word = 1;
    endianness = *(char *)&word ? IS_LITTLE_ENDIAN : IS_BIG_ENDIAN;
    io_func *io = openFlatFile(argv[1]);
    if (!io)
        return 1;
    Volume *v = openVolume(io);
    if (!v) {
        CLOSE(io);
        return 1;
    }
    int ok = 0;
    HFSPlusCatalogRecord *rec = NULL;
    if (!strcmp(argv[2], "attr-set") && argc == 6 && strlen(argv[4]) < 255 && strlen(argv[5]) < 1024) {
        rec = getRecordFromPath3(argv[3], v, NULL, NULL, TRUE, FALSE, kHFSRootFolderID);
        if (rec && (rec->recordType == kHFSPlusFileRecord || rec->recordType == kHFSPlusFolderRecord)) {
            HFSCatalogNodeID id = rec->recordType == kHFSPlusFileRecord ? ((HFSPlusCatalogFile *)rec)->fileID
                                                                        : ((HFSPlusCatalogFolder *)rec)->folderID;
            ok = setAttribute(v, id, argv[4], (uint8_t *)argv[5], strlen(argv[5]));
        }
    } else if (!strcmp(argv[2], "attr-get") && argc == 5) {
        rec = getRecordFromPath3(argv[3], v, NULL, NULL, TRUE, FALSE, kHFSRootFolderID);
        if (rec && (rec->recordType == kHFSPlusFileRecord || rec->recordType == kHFSPlusFolderRecord)) {
            HFSCatalogNodeID id = rec->recordType == kHFSPlusFileRecord ? ((HFSPlusCatalogFile *)rec)->fileID
                                                                        : ((HFSPlusCatalogFolder *)rec)->folderID;
            uint8_t *data = NULL;
            size_t size = getAttribute(v, id, argv[4], &data);
            if (data && size < 1024 * 1024) {
                ok = fwrite(data, 1, size, stdout) == size;
            }
            free(data);
        }
    } else if (!strcmp(argv[2], "symlink") && argc == 5) {
        ok = makeSymlink(argv[3], argv[4], v);
    } else if (!strcmp(argv[2], "readlink")) {
        rec = getRecordFromPath3(argv[3], v, NULL, NULL, TRUE, FALSE, kHFSRootFolderID);
        if (rec && rec->recordType == kHFSPlusFileRecord) {
            HFSPlusCatalogFile *file = (HFSPlusCatalogFile *)rec;
            if ((file->permissions.fileMode & 0170000) == 0120000 && file->dataFork.logicalSize < 1024) {
                char text[1024] = {0};
                io_func *raw = openRawFile(file->fileID, &file->dataFork, rec, v);
                if (raw) {
                    ok = READ(raw, 0, (size_t)file->dataFork.logicalSize, text);
                    CLOSE(raw);
                    if (ok)
                        puts(text);
                }
            }
        }
    } else if (!strcmp(argv[2], "copy-tree") && argc == 6) {
        io_func *other = openFlatFile(argv[4]);
        if (other) {
            Volume *dst = openVolume(other);
            if (dst) {
                ok = copy_tree(v, dst, argv[3], argv[5], 0);
                closeVolume(dst);
            }
            CLOSE(other);
        }
    } else if (!strcmp(argv[2], "rm")) {
        ok = removeFile(argv[3], v);
    } else if (!strcmp(argv[2], "ls")) {
        rec = getRecordFromPath(argv[3], v, NULL, NULL);
        if (rec && rec->recordType == kHFSPlusFolderRecord) {
            CatalogRecordList *head = getFolderContents(((HFSPlusCatalogFolder *)rec)->folderID, v), *it = head;
            ok = 1;
            for (; it; it = it->next) {
                if (it->record->recordType != kHFSPlusFileRecord && it->record->recordType != kHFSPlusFolderRecord)
                    continue;
                char *name = unicodeToAscii(&it->name);
                if (!name || strchr(name, '\n') || strchr(name, '\r')) {
                    free(name);
                    ok = 0;
                    break;
                }
                puts(name);
                free(name);
            }
            releaseCatalogRecordList(head);
        }
    } else if (!strcmp(argv[2], "grow")) {
        grow_hfs(v, strtoull(argv[3], NULL, 10));
        ok = 1;
    } else if (!strcmp(argv[2], "mkdir")) {
        rec = getRecordFromPath(argv[3], v, NULL, NULL);
        ok = rec ? rec->recordType == kHFSPlusFolderRecord : newFolder(argv[3], v) != 0;
        if (ok && !rec && argc == 6) {
            rec = getRecordFromPath(argv[3], v, NULL, NULL);
            if (!rec || rec->recordType != kHFSPlusFolderRecord)
                ok = 0;
            else {
                HFSPlusCatalogFolder *folder = (HFSPlusCatalogFolder *)rec;
                folder->permissions.ownerID = (uint32_t)strtoul(argv[5], NULL, 10);
                folder->permissions.groupID = folder->permissions.ownerID;
                folder->permissions.fileMode = 0040000 | (uint16_t)strtoul(argv[4], NULL, 8);
                ok = updateCatalog(v, rec);
            }
        }
    } else if (!strcmp(argv[2], "get") && argc == 5) {
        rec = getRecordFromPath(argv[3], v, NULL, NULL);
        if (rec && rec->recordType == kHFSPlusFileRecord) {
            FILE *f = fopen(argv[4], "wb");
            if (f) {
                AbstractFile *a = createAbstractFileFromFile(f);
                writeToFile((HFSPlusCatalogFile *)rec, a, v);
                a->close(a);
                ok = 1;
            }
        }
    } else if (!strcmp(argv[2], "put") && argc == 7) {
        FILE *f = fopen(argv[4], "rb");
        if (f) {
            ok = add_hfs(v, createAbstractFileFromFile(f), argv[3]);
            if (ok) {
                rec = getRecordFromPath(argv[3], v, NULL, NULL);
                if (!rec || rec->recordType != kHFSPlusFileRecord)
                    ok = 0;
                else {
                    HFSPlusCatalogFile *file = (HFSPlusCatalogFile *)rec;
                    file->permissions.ownerID = (uint32_t)strtoul(argv[6], NULL, 10);
                    file->permissions.groupID = file->permissions.ownerID;
                    file->permissions.fileMode =
                        (file->permissions.fileMode & 0170000) | (uint16_t)strtoul(argv[5], NULL, 8);
                    ok = updateCatalog(v, rec);
                }
            }
        }
    }
    free(rec);
    closeVolume(v);
    CLOSE(io);
    if (!ok)
        fprintf(stderr, "HFS operation failed: %s %s\n", argv[2], argv[3]);
    return ok ? 0 : 1;
}
