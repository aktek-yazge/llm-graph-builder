import { Dropzone, Flex, SpotlightTarget, Typography } from '@neo4j-ndl/react';
import { useState, FunctionComponent, useEffect } from 'react';
import Loader from '../../../utils/Loader';
import { v4 as uuidv4 } from 'uuid';
import { useCredentials } from '../../../context/UserCredentials';
import { useFileContext } from '../../../context/UsersFiles';
import { CustomFile, CustomFileBase } from '../../../types';
import { buttonCaptions, chunkSize } from '../../../utils/Constants';
import { InformationCircleIconOutline } from '@neo4j-ndl/react/icons';
import { IconButtonWithToolTip } from '../../UI/IconButtonToolTip';
import { uploadAPI } from '../../../utils/FileAPI';
import { normalizeFileName } from '../../../utils/utf8';
import { showErrorToast, showSuccessToast } from '../../../utils/Toasts';

const DropZone: FunctionComponent = () => {
  const { filesData, setFilesData, model, generateEmbedding } = useFileContext();
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const { userCredentials } = useCredentials();
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const onDropHandler = (f: Partial<globalThis.File>[]) => {
    setIsLoading(false);
    console.log(`📁 Files dropped: ${f.length} files`);

    // Dosya validasyonu
    const validFiles: File[] = [];
    f.forEach((file, index) => {
      // Dosya undefined kontrolü
      if (!file || !file.name) {
        console.error(`❌ File ${index + 1} is invalid or missing name - skipping`);
        return;
      }

      console.log(
        `📄 File ${index + 1}: ${file.name}, Size: ${file.size ?? 'undefined'} bytes, Type: ${file.type}, Last Modified: ${file.lastModified ? new Date(file.lastModified).toISOString() : 'Unknown'}`
      );

      // Dosya boyutu kontrolü
      if (!file.size || file.size === 0) {
        console.error(`❌ File ${file.name} is empty (${file.size ?? 'undefined'} bytes) - skipping`);
        showErrorToast(`File "${file.name}" is empty and cannot be uploaded`);
      } else if (file.size > 100 * 1024 * 1024) {
        // 100MB limit
        console.error(`❌ File ${file.name} is too large (${file.size} bytes) - skipping`);
        showErrorToast(`File "${file.name}" is too large (max 100MB allowed)`);
      } else {
        validFiles.push(file as File);
        console.log(`✅ File ${file.name} passed validation`);
      }
    });

    if (validFiles.length === 0) {
      console.warn(`⚠️ No valid files to upload`);
      return;
    }

    setSelectedFiles(validFiles);

    if (validFiles.length) {
      const defaultValues: CustomFileBase = {
        processingTotalTime: 0,
        status: 'None',
        nodesCount: 0,
        relationshipsCount: 0,
        model: model,
        fileSource: 'local file',
        uploadProgress: 0,
        processingProgress: undefined,
        retryOptionStatus: false,
        retryOption: '',
        chunkNodeCount: 0,
        chunkRelCount: 0,
        entityNodeCount: 0,
        entityEntityRelCount: 0,
        communityNodeCount: 0,
        communityRelCount: 0,
        createdAt: new Date(),
      };

      const copiedFilesData: CustomFile[] = [...filesData];
      for (let index = 0; index < validFiles.length; index++) {
        const file = validFiles[index];
        const filedataIndex = copiedFilesData.findIndex((filedataitem) => filedataitem?.name === file?.name);
        if (filedataIndex == -1) {
          copiedFilesData.unshift({
            name: file.name,
            // @ts-ignore
            type: `${file.name.substring(file.name.lastIndexOf('.') + 1, file.name.length).toUpperCase()}`,
            size: file.size || 0,
            uploadProgress: file.size && file.size < chunkSize ? 100 : 0,
            id: uuidv4(),
            ...defaultValues,
          });
        } else {
          const tempFileData = copiedFilesData[filedataIndex];
          copiedFilesData.splice(filedataIndex, 1);
          copiedFilesData.unshift({
            ...tempFileData,
            status: defaultValues.status,
            nodesCount: defaultValues.nodesCount,
            relationshipsCount: defaultValues.relationshipsCount,
            processingTotalTime: defaultValues.processingTotalTime,
            model: defaultValues.model,
            fileSource: defaultValues.fileSource,
            processingProgress: defaultValues.processingProgress,
          });
        }
      }
      copiedFilesData.sort((a, b) => (a.name ?? '').localeCompare(b.name ?? ''));
      setFilesData(copiedFilesData);
    }
  };
  useEffect(() => {
    if (selectedFiles.length > 0) {
      selectedFiles.forEach((file) => {
        uploadFileInChunks(file);
      });
    }
  }, [selectedFiles]);

  const uploadFileInChunks = (file: File) => {
    console.log(`🚀 Starting chunked upload for file: ${file.name}, Size: ${file.size} bytes`);

    // Dosya boyutu kontrolü
    if (file.size === 0) {
      console.error(`❌ File ${file.name} has 0 bytes - cannot upload empty file`);
      showErrorToast(`File ${file.name} appears to be empty and cannot be uploaded`);
      return;
    }

    // Dosya tipini kontrol et
    console.log(
      `📋 File details - Name: ${file.name}, Type: ${file.type}, Size: ${file.size} bytes, Last Modified: ${file.lastModified ? new Date(file.lastModified).toISOString() : 'Unknown'}`
    );

    const totalChunks = Math.ceil(file.size / chunkSize);
    const chunkProgressIncrement = 100 / totalChunks;
    console.log(
      `📊 Upload plan - Total chunks: ${totalChunks}, Chunk size: ${chunkSize}, Progress increment: ${chunkProgressIncrement}%`
    );
    let chunkNumber = 1;
    let start = 0;
    let end = chunkSize;
    const uploadNextChunk = async () => {
      if (chunkNumber <= totalChunks) {
        const chunk = file.slice(start, end);
        console.log(`📤 Preparing chunk ${chunkNumber}/${totalChunks} for file: ${file.name}`);
        console.log(`📏 Chunk details - Start: ${start}, End: ${end}, Actual chunk size: ${chunk.size} bytes`);

        // Chunk boyutu kontrolü
        if (chunk.size === 0) {
          console.error(`❌ Chunk ${chunkNumber}/${totalChunks} has 0 bytes for file: ${file.name}`);
          showErrorToast(`Empty chunk detected for file ${file.name}`);
          return;
        }

        const formData = new FormData();
        formData.append('file', chunk);
        formData.append('chunkNumber', chunkNumber.toString());
        formData.append('totalChunks', totalChunks.toString());

        // Dosya ismini UTF-8 olarak encode ettiğimizden emin olalım
        console.log(`🔤 Original filename: ${file.name}`);
        console.log(`🔤 Filename bytes: ${new TextEncoder().encode(file.name)}`);

        // FormData otomatik olarak UTF-8 encoding kullanır ama debug için kontrol edelim
        formData.append('originalname', file.name);
        console.log(`📋 FormData'ya eklenen filename: ${file.name}`);

        formData.append('model', model);
        for (const key in userCredentials) {
          formData.append(key, userCredentials[key]);
        }
        setIsLoading(true);
        setFilesData((prevfiles) =>
          prevfiles.map((curfile) => {
            if (normalizeFileName(curfile.name) === normalizeFileName(file.name)) {
              return {
                ...curfile,
                status: 'Uploading',
              };
            }
            return curfile;
          })
        );
        try {
          console.log(
            `📤 Uploading chunk ${chunkNumber}/${totalChunks} for file: ${file.name}, chunk size: ${chunk.size} bytes`
          );
          const apiResponse = await uploadAPI(chunk, model, chunkNumber, totalChunks, file.name, generateEmbedding);
          console.log(
            `📥 Upload API response for chunk ${chunkNumber}/${totalChunks}:`,
            JSON.stringify(apiResponse, null, 2)
          );

          if (apiResponse?.status === 'Failed') {
            console.error(`❌ Upload failed for chunk ${chunkNumber}/${totalChunks}:`, apiResponse);
            throw new Error(`message:${apiResponse.data.message},fileName:${apiResponse.data.file_name}`);
          } else {
            console.log(`✅ Chunk ${chunkNumber}/${totalChunks} uploaded successfully for ${file.name}`);
            if (apiResponse.data) {
              console.log(`📊 API response data received:`, apiResponse.data);
              setFilesData((prevfiles) =>
                prevfiles.map((curfile) => {
                  if (normalizeFileName(curfile.name) === normalizeFileName(file.name)) {
                    return {
                      ...curfile,
                      uploadProgress: Math.ceil(chunkNumber * chunkProgressIncrement),
                    };
                  }
                  return curfile;
                })
              );
            } else {
              console.warn(`⚠️ No data in API response for chunk ${chunkNumber}/${totalChunks}`);
            }
            setFilesData((prevfiles) =>
              prevfiles.map((curfile) => {
                if (normalizeFileName(curfile.name) === normalizeFileName(file.name)) {
                  return {
                    ...curfile,
                    uploadProgress: Math.ceil(chunkNumber * chunkProgressIncrement),
                  };
                }
                return curfile;
              })
            );
            chunkNumber++;
            start = end;
            if (start + chunkSize < file.size) {
              end = start + chunkSize;
            } else {
              end = file.size + 1;
            }
            uploadNextChunk();
          }
        } catch (error) {
          setIsLoading(false);
          if (error instanceof Error) {
            showErrorToast(`Error Occurred: ${error.message}`, true);
          }
          setFilesData((prevfiles) =>
            prevfiles.map((curfile) => {
              if (normalizeFileName(curfile.name) === normalizeFileName(file.name)) {
                return {
                  ...curfile,
                  status: 'Upload Failed',
                  type: `${file.name.substring(file.name.lastIndexOf('.') + 1, file.name.length).toUpperCase()}`,
                };
              }
              return curfile;
            })
          );
        }
      } else {
        console.log(
          `🎉 All ${totalChunks} chunks uploaded successfully for ${file.name}. Total file size: ${file.size} bytes`
        );
        console.log(`📝 Setting file status to "New" for: ${file.name}`);
        setFilesData((prevfiles) =>
          prevfiles.map((curfile) => {
            if (normalizeFileName(curfile.name) === normalizeFileName(file.name)) {
              console.log(`✅ File status updated to "New" for: ${curfile.name}`);
              return {
                ...curfile,
                status: 'New',
                uploadProgress: 100,
                createdAt: new Date(),
              };
            }
            return curfile;
          })
        );
        setIsLoading(false);
        console.log(`🔔 Showing success toast for: ${file.name}`);
        showSuccessToast(`${file.name} uploaded successfully`);
      }
    };

    uploadNextChunk();
  };

  return (
    <>
      <SpotlightTarget
        id='dropzone'
        hasPulse={true}
        indicatorVariant='border'
        hasAnchorPortal={false}
        borderRadius={11}
      >
        <Dropzone
          loadingComponent={isLoading && <Loader title='Uploading' />}
          isTesting={true}
          className='bg-none! dropzoneContainer'
          supportedFilesDescription={
            <Typography variant='body-small'>
              <Flex>
                <span>{buttonCaptions.dropzoneSpan}</span>
                <div className='align-self-center'>
                  <IconButtonWithToolTip
                    label='Source info'
                    clean
                    text={
                      <Typography variant='body-small'>
                        <Flex gap='3' alignItems='flex-start'>
                          <span>Microsoft Office (.docx, .pptx, .xls, .xlsx)</span>
                          <span>PDF (.pdf)</span>
                          <span>Images (.jpeg, .jpg, .png, .svg)</span>
                          <span>Text (.html, .txt , .md)</span>
                        </Flex>
                      </Typography>
                    }
                  >
                    <InformationCircleIconOutline className='w-[22px] h-[22px]' />
                  </IconButtonWithToolTip>
                </div>
              </Flex>
            </Typography>
          }
          dropZoneOptions={{
            accept: {
              'application/pdf': ['.pdf'],
              'image/*': ['.jpeg', '.jpg', '.png', '.svg'],
              'text/html': ['.html'],
              'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
              'text/plain': ['.txt'],
              'application/vnd.ms-powerpoint': ['.pptx'],
              'application/vnd.ms-excel': ['.xls'],
              'text/markdown': ['.md'],
              'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
            },
            onDrop: (f: Partial<globalThis.File>[]) => {
              onDropHandler(f);
            },
            onDropRejected: (e) => {
              if (e.length) {
                showErrorToast('Failed To Upload, Unsupported file extention');
              }
            },
          }}
        />
      </SpotlightTarget>
    </>
  );
};

export default DropZone;
